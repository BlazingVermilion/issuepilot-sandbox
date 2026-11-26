# Evidence for a portfolio walkthrough

Start with [the verified intake and reviewer walkthrough](../../README.md#run-on-windows). Pick one case and use its original issue number throughout. Keep the caption honest about the difference between historical source data and a live GitHub write.

| Capture | What to show | What the viewer can verify |
|---|---|---|
| 1. Issue intake | The three connected nodes in your existing published n8n workflow and a successful execution | The signed event went through the actual transport. Compare the execution ID with the case's RECEIVED event. |
| 2. Review | Original ticket and proposal side by side; open the evidence dialog for a supporting policy section | Which model read the ticket, which rule supports the recommendation, and what the maintainer can change. |
| 3. Outcome | Open audit history for the same issue from Inbox. Show the reviewer's decision and the resulting local action. | The reviewer approved a particular version, and an actual result was recorded. |
| 4. Scale and operations | Output of `manage.py status`; separately, the executed Snowsight result or a green Actions run with commit/digest | The received, proposed and reviewed counts can differ. Only claim external execution if you have its own evidence. |

For LinkedIn, the first three images tell the story. In GitHub, include operational evidence when available. Hide tokens, keys, account details and environment settings. Do not use a synthetic benchmark result as a production screenshot.
