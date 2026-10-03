"""Experience engine for مهاد memory: turns finished sessions into checked, reusable experience.

Seven parts, each in its own module:
  checkers.py     experience as executable verifiers (fail before the fix, pass after)
  review.py       a reviewer at the decision point, before the agent finishes
  corrections.py  lessons from the user's final version of the work
  failures.py     failure-path memory and live detection during a session
  dream.py        practice tasks from mutated past fixes, and A/B tests of lessons
  skills.py       repeated command sequences compiled into verified skills
  competence.py   a per-family competence record that sets the amount of scaffolding
"""
