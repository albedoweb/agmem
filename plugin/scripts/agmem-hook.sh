#!/bin/sh
# UserPromptSubmit → inject task-relevant agmem context into the prompt.
#
# Silent no-op when the agmem binary isn't installed; `agmem hook inject`
# itself no-ops when the current repo has no .agmem/ (not initialized),
# when the prompt is too short, or when context was injected recently
# (turn-based throttle). Never blocks the prompt.
command -v agmem >/dev/null 2>&1 || exit 0
exec agmem hook inject
