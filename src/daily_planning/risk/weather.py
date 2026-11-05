"""Scenario-local weather closures layered over an immutable source series."""
from __future__ import annotations

from datetime import datetime, timedelta


def merge_blocks(blocks):
    ordered = sorted((start, end) for start, end in blocks if end > start)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = merged[-1][0], max(end, merged[-1][1])
        else:
            merged.append((start, end))
    return merged


class ScenarioWeather:
    def __init__(self, source, blocks):
        self.source = source
        self.blocks = merge_blocks(blocks)
        self.total = source.total

    def slot(self, at, rule):
        start, end, datum = self.source.slot(at, rule)
        for boundary in (point for block in self.blocks for point in block):
            if start < boundary <= at:
                start = boundary
            elif at < boundary < end:
                end = boundary
        return start, end, datum

    def blocked(self, at, rule):
        return self.source.blocked(at, rule) or any(a <= at < b for a, b in self.blocks)

    def first_workable(self, start, rule):
        at = start
        seen = 0
        while self.blocked(at, rule):
            _, end, _ = self.slot(at, rule)
            at = end
            seen += 1
            if seen > self.source.total * 4 + len(self.blocks) * 2:
                raise ValueError('No workable scenario weather slot found')
        return at


def scenario_weather(source, scenario, as_of):
    duration = scenario.get('additional_weather_block_minutes', 0)
    onset = scenario.get('weather_block_onset_offset_minutes')
    onset_seconds = scenario.get('weather_block_onset_offset_seconds', 0)
    if type(duration) is not int or duration < 0 or type(onset_seconds) is not int:
        raise ValueError('Weather block duration and onset must use integer minutes/seconds')
    if not duration:
        if onset not in (None, 0) or onset_seconds:
            raise ValueError('Weather onset requires a positive block duration')
        return ScenarioWeather(source, [])
    if type(onset) is not int:
        raise ValueError('Weather block onset is required in integer minutes')
    start = as_of + timedelta(minutes=onset, seconds=onset_seconds)
    return ScenarioWeather(source, [(start, start + timedelta(minutes=duration))])
