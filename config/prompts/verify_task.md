## Current task
- Title: {{task_title}}
- Description: {{task_description}}
- Work instructions: {{task_instructions}}
- Expected duration: {{expected_time}}
- Success message: {{success_message}}

## Cameras in this analysis
{{stream_list}}

## Frames
The images below were captured at {{timestamp}} (most recent first). Each image
is preceded by the camera it came from.

## Decision
Decide whether the current task is fully completed.

Return exactly one JSON object with this shape:

{
  "task_completed": true,
  "confidence": 0.0,
  "reasoning": "One or two sentences describing the visible evidence.",
  "evidence": ["short observation", "another observation"]
}

- "task_completed": true only when the end state of the task is clearly visible.
- "confidence": 0.0–1.0, how certain you are of your own verdict.
- "reasoning": concise, cite what is visible in which camera view.
- "evidence": up to five short bullet observations.
- No markdown, no code fences, no text outside the JSON object.
