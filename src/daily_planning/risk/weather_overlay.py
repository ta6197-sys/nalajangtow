"""Scenario-local weather closures layered over an immutable source series."""
from __future__ import annotations

from datetime import datetime, timedelta


def merge_blocks(blocks):
    ordered = sorted(blocks)
    merged = []
    for start, end in ordered:
        if end <= start:
            raise ValueError('Weather block must have positive duration')
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
    blocks = []
    if duration:
        if type(onset) is not int:
            raise ValueError('Weather block onset is required in integer minutes')
        start = as_of + timedelta(minutes=onset, seconds=onset_seconds)
        blocks.append((start, start + timedelta(minutes=duration)))
    elif onset not in (None, 0) or onset_seconds:
        raise ValueError('Weather onset requires a positive block duration')
    additional = scenario.get('weather_blocks', [])
    if not isinstance(additional, list):
        raise ValueError('weather_blocks must be a list')
    for index, item in enumerate(additional):
        if not isinstance(item, dict):
            raise ValueError(f'Invalid weather block {index}')
        minute = item.get('onset_offset_minutes')
        second = item.get('onset_offset_seconds', 0)
        length_min = item.get('duration_minutes', 0)
        length_sec = item.get('duration_seconds', 0)
        if any(type(value) is not int for value in (minute, second, length_min, length_sec)):
            raise ValueError(f'Weather block {index} requires integer minutes/seconds')
        length = timedelta(minutes=length_min, seconds=length_sec)
        if length <= timedelta(0):
            raise ValueError(f'Weather block {index} requires positive duration')
        start = as_of + timedelta(minutes=minute, seconds=second)
        blocks.append((start, start + length))
    return ScenarioWeather(source, blocks)
