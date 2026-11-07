# Project
AI music platform MVP. Local web UI + local FastAPI server + RunPod GPU worker running YuE2.
The spec is docs/PRD.md. Read only the sections relevant to the current milestone; do not paste it all into context.

# Rules (binding)
- The interfaces and API contracts in PRD section 7 and section 6 are fixed. If they seem wrong, stop and tell me; do not silently change them.
- Work one milestone at a time (PRD section 13). Stop at the end of the milestone. Do not start the next one.
- Never require a GPU for tests. Use FakeEngine. Real YuE2 is only verified by me in M6.
- Do not add features outside PRD scope (section 2). Add extension points only as the PRD describes them.
- Do not guess YuE2 API details. Use docs/yue2-api-notes.md (written in M0) and the wheel source.
- Run the make targets from PRD section 10 (lint, typecheck, test) before saying anything is done. Show the output.
- In your final message, say plainly what you could NOT verify.

# Workflow
- Commit at the end of each milestone with a descriptive message.
- When compacting, always preserve: the current milestone, the list of modified files, and the test commands.