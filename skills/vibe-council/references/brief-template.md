# Member brief template

Write the brief to the session folder. Aim for about 1,500 to 3,000 words. Keep the
sections in this order. Replace every `<…>`. Never put secrets or personal data in it,
and never put the brief's own folder path in it.

```markdown
# Council brief: <topic>

## Role
You are one of <N> council members (<seat names>). Answer independently. You cannot
see the other members' answers.

## The user's standing instructions
<Verbatim. Do not paraphrase.>

## Bias
Weigh optimistic bias as well as pessimistic bias. Flag an item that flatters the
result as readily as one that is too cautious.

## Context
<Half a page at most. Point to files by path; do not paste them.>

## Settled rules (not up for a vote)
<Rules already decided. You may comment on how they compound, not on whether to keep them.>

## Measured facts
<Facts with numbers, each with how it was measured.>

## Items
<One entry per item:>
- <ID> — <decision, one or two lines>. Direction: <neutral | stricter | looser |
  pessimistic | optimistic>. If wrong: <consequence>.

## Output format (strict)
Answer with at most 500 words, in this order:

1. One line per item, exactly:
   `<ID>: KEEP | CHANGE: <simplest change> | DROP | DEFER — <short reason>`
   Pick one verdict per line. Give a change only after CHANGE.
2. `Compounding`: 2 to 4 lines on how the items interact.
3. `Top 3`: the three items that matter most.

## Constraints
- Read-only. No edits, no Git writes, no subagents.
- Forbidden paths: <secrets, credentials and private data, plus the project-specific
  paths the coordinator lists>. Do not read them. In a round 2 brief, also list the
  session's `answers/` folder.
- Use the network only if you need it.
- Use about 15 tool calls.
- Answer in your final message.
```
