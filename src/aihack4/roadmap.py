"""로드맵: 맞춤 전략에서 고른 방법의 단계별 작업을 날짜에 놓는다(LLM 없음). 설계: docs/백엔드_설계_v3.md

- 제출물은 공고 카드 순서대로, 각 제출물은 고른 방법의 단계 순서대로 한다.
- 고르게: 필요한 시간을 여유 기간 전 작업일에 같은 비율로 나눈다(적어 낸 시간을 매일 꽉 채우지 않는다).
- 한 번 작업은 최소 1시간(하루에 쓸 수 있는 시간이 1시간보다 적으면 그 시간). 못 쓰는 날은 이미 작업일에서 빠져 있다.
- 마감 전 여유 기간에는 서류 작성·최종 점검·제출만 한다.
- 제출물이 끝나는 날이 중간 목표다.
- progress(이미 한 시간)를 주면 그만큼 빼고 남은 일만 다시 놓는다.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from aihack4.feasibility import Profile
from aihack4.strategy import Strategy

MIN_SESSION = 1.0
FINAL_NAME = "서류 작성·최종 점검·제출"


@dataclass
class Item:
    submission: str
    step: str
    hours: float


@dataclass
class Day:
    date: date
    capacity: float  # 그날 쓸 수 있는 시간(팀이면 팀 전체)
    items: list[Item] = field(default_factory=list)
    buffer: bool = False

    @property
    def hours(self) -> float:
        return round(sum(i.hours for i in self.items), 1)


@dataclass
class Roadmap:
    days: list[Day]
    milestones: list[tuple[str, date]]  # (제출물, 끝나는 날)
    pace: float  # 쓸 수 있는 시간 중 실제로 쓰는 비율
    overflow: float  # 작업일에 다 못 넣은 시간(어려움일 때)
    done: dict[str, float] = field(default_factory=dict)


def step_key(submission: str, step: str) -> str:
    return f"{submission} / {step}"


def queue(strategy: Strategy, progress: dict[str, float] | None) -> list[Item]:
    """할 일 순서. 단계 시간에는 고른 방법의 보정(첫 참가, 역량 보완)을 같은 비율로 반영한다."""
    progress = progress or {}
    items = []
    for choice in strategy.choices:
        if not choice.option:
            continue
        scale = choice.hours / choice.option["hours"] if choice.option["hours"] else 1.0
        for step in choice.option["steps"]:
            hours = round(step["hours"] * scale, 2) - progress.get(step_key(choice.submission, step["name"]), 0.0)
            if hours >= 0.05:
                items.append(Item(choice.submission, step["name"], hours))
    return items


def build(strategy: Strategy, profile: Profile, progress: dict[str, float] | None = None) -> Roadmap:
    work = [Day(d, round(profile.hours_on(d), 2)) for d in strategy.work_days]
    buffer = [Day(d, round(profile.hours_on(d), 2), buffer=True) for d in strategy.buffer_days]
    todo = queue(strategy, progress)
    need = sum(i.hours for i in todo)
    capacity = sum(d.capacity for d in work)
    pace = min(1.0, need / capacity) if capacity else 1.0

    # 하루 몫(그날 시간 × pace)을 쌓아 두었다가, 최소 작업 시간 이상 모이면 그날 쓴다.
    budgets, carry = [], 0.0
    for index, day in enumerate(work):
        carry += day.capacity * pace
        remaining = need - sum(budgets)
        floor = min(MIN_SESSION, day.capacity, remaining)
        amount = min(day.capacity, carry)
        if index == len(work) - 1:  # 마지막 작업일: 남은 몫을 그날 시간까지 다 쓴다
            amount = min(day.capacity, remaining)
        amount = min(amount, remaining)
        if amount + 1e-9 >= floor and amount > 0:
            budgets.append(amount)
            carry -= amount
        else:
            budgets.append(0.0)
    overflow = round(need - sum(budgets), 1)
    if overflow > 0.05 and work:  # 시간이 모자라면(어려움) 남은 시간을 작업일에 고르게 더 얹는다
        extra = overflow / len(work)
        budgets = [b + extra for b in budgets]

    milestones = _fill(work, budgets, todo)
    _place_final(buffer or work[-1:], strategy.final_hours)
    return Roadmap(work + buffer, milestones, round(pace, 2), max(0.0, overflow), dict(progress or {}))


def _fill(work: list[Day], budgets: list[float], todo: list[Item]) -> list[tuple[str, date]]:
    """하루 몫만큼 순서대로 할 일을 넣는다. 한 단계가 여러 날에 걸칠 수 있다."""
    left = [Item(i.submission, i.step, i.hours) for i in todo]
    finished: dict[str, date] = {}
    pointer = 0
    for day, budget in zip(work, budgets):
        while budget > 1e-6 and pointer < len(left):
            item = left[pointer]
            take = min(budget, item.hours)
            day.items.append(Item(item.submission, item.step, round(take, 2)))
            item.hours -= take
            budget -= take
            if item.hours <= 1e-6:
                pointer += 1
                last_of_submission = pointer == len(left) or left[pointer].submission != item.submission
                if last_of_submission:
                    finished[item.submission] = day.date
    return list(finished.items())


def _place_final(days: list[Day], hours: float) -> None:
    """여유 기간 앞쪽부터 최종 점검·제출을 넣는다."""
    for day in days:
        if hours <= 1e-6:
            return
        take = min(hours, max(day.capacity, MIN_SESSION))
        day.items.append(Item("최종", FINAL_NAME, round(take, 2)))
        hours -= take


def weeks(roadmap: Roadmap, today: date) -> list[dict]:
    """기준일부터 7일 단위로 묶는다(실행 계획 화면). 작업이 없는 주는 쉬는 주."""
    if not roadmap.days:
        return []
    out, start = [], today
    last = roadmap.days[-1].date
    while start <= last:
        end = start + timedelta(days=6)
        totals: dict[str, float] = {}
        for day in roadmap.days:
            if start <= day.date <= end:
                for item in day.items:
                    totals[item.step] = totals.get(item.step, 0.0) + item.hours
        goals = [name for name, when in roadmap.milestones if start <= when <= end]
        buffer = any(d.buffer for d in roadmap.days if start <= d.date <= end)
        out.append({"start": start, "end": min(end, last), "tasks": totals, "milestones": goals, "buffer": buffer})
        start = end + timedelta(days=1)
    return out
