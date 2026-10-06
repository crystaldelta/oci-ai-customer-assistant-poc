# Document templates

Copy a template into `docs/` (or into the message or email it is for), fill the `<…>` fields and
delete the guidance comments (the `<!-- … -->` blocks). "Phase 0" and the other phases are in
[PLAN.md](../PLAN.md); an "AD" is an entry in [ARCHITECTURE.md](../ARCHITECTURE.md)'s decision log. Australian English, professional tone. Pricing or contract questions
go to Crystal Delta's Sales team, never into these documents.

| Template | Audience | When |
| --- | --- | --- |
| [features.md](features.md) | Client and executives | Copy to `docs/features.md` in Phase 0; update with each delivered feature |
| [feature-register.md](feature-register.md) | The team | Copy to `docs/feature-register.md` in Phase 0; the internal record behind `features.md` |
| [devops-request.md](devops-request.md) | DevOps | Day one: the staged request for cloud resources |
| [crawl-permission.md](crawl-permission.md) | Client | Before any request to the client's website |
| [safety-wording-approval.md](safety-wording-approval.md) | Client | Before the first demo: sign-off of the fixed safety replies |
| [data-residency-exception.md](data-residency-exception.md) | Client and the team | When any service must run outside the agreed country; also recorded as an AD |
| [demo-covering-message.md](demo-covering-message.md) | Demo recipients | When sending the demo link, credentials and capability overview |
| [retrospective.md](retrospective.md) | Crystal Delta | At each milestone; feeds estimates and the `ai-project` skill |

## Keeping the features document current

`features.md` is what the client and executives read; `feature-register.md` is how the team keeps
it honest.

1. Add a row to the register when a feature is planned (status `Planned`).
2. When it is built and checked, set `Built` with the evidence (commit, date, what was checked).
3. When it runs in the environment the client sees, set `Live`, and add or update its dot point in
   `features.md` in the same commit.
4. Only `Live` features appear in `features.md`. Never describe a planned feature there as built.
5. Review both at each milestone and before sending `features.md` to anyone.
