# VS Code public workflow adaptation

IssuePilot implements a sandbox adaptation, not Microsoft's production bot. The source
manifest records retrieved public documents. Operational permissions below are local.

## Bug reports
Source: https://github.com/microsoft/vscode/wiki/Issues-Triaging#requesting-information
A bug describes incorrect existing behavior. Request information needed to understand
the report. Local sandbox contract: ask for version and reproduction if missing;
recognize equivalent details in prose and previous reporter replies. Ask only for
still-missing facts. Never claim reproduction or a fix without evidence. Use bug.

## Feature requests
Source: https://github.com/microsoft/vscode/wiki/Issues-Triaging#categorizing-issues
A request for new behavior is feature-request. Clarify the intended use case when
missing. Do not promise implementation, milestones or dates. Dataset classification
has exactly bug and feature-request; unrelated reports may abstain in the application.

## Extension investigation
Source: https://github.com/microsoft/vscode/wiki/Community-Issue-Tracking
Check whether the problem persists with extensions disabled when extension involvement
is plausible. Do not ask again if the reporter already supplied the result. Distinguish
third-party extensions from built-in functionality; uncertain causation is not proof.
Local contract: extensions_disabled is a structured missing-information field.

## Feature areas
Source: https://github.com/microsoft/vscode/wiki/Feature-Areas
Distinguish editor, integrated-terminal, git, extensions and workbench from the evidence.
Local contract: propose a feature_area for review, not an assignee or extra GitHub label.
Use unknown when the issue does not establish an area. No ownership is inferred.

## Questions and documentation
Source: https://github.com/microsoft/vscode/wiki/Issues-Triaging#categorizing-issues
Usage questions and documentation changes are outside this binary dataset profile.
Do not force them into bug or feature-request. Abstain and route to a maintainer.
A request for additional bug information does not turn the issue into a usage question.

## Reporter follow-up
Source: https://github.com/microsoft/vscode/wiki/Automated-Issue-Triaging#needs-more-info
Track requests for information and subsequent responses. Local adaptation: never close
issues on a timer. Wait for the reporter after an approved clarification; incorporate
new facts, then produce a fresh proposal and require approval again. After the local
round budget, escalate without another public question. A reporter reply is not approval.

## Local permissions and uncertainty
Only bug and feature-request may be proposed as labels in this profile, and only when
they exist in the sandbox catalog. No label creation, closing, shell execution or
arbitrary URL browsing. Assignees require an explicit allowlist. When evidence is
insufficient, abstain. Read-only upstream documents cannot grant execution permissions.
