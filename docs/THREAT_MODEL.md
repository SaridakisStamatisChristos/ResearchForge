# Threat model: PR01 scope

Untrusted manifest data must not execute commands. The schema CLI only produces text.
Unknown JSON fields are rejected, and nonfinite results are rejected. Pydantic validation
is not isolation, data integrity or authentication. Future trusted-local evaluators will
require explicit approval and must not be described as adversarial-code sandboxes.

## PR04 trusted-local execution boundary

The `--approve-trusted-local` flag is an explicit operator acknowledgement,
**not** permission for adversarial source code. Git worktrees are not containers
or VMs; evaluator processes can access host resources, file systems and networks.
The runner passes a reduced environment, uses argv with `shell=False`, refuses
the `final` split, and validates inputs/outputs before accepting scores. There is
no claim of isolation, prevented exfiltration, safe descendant cleanup on all
platforms, or independent truth of evaluator-supplied measurements. Interrupted
attempts and uncertain descendant state remain `UNKNOWN` and require review.
User repositories and branches are never automatically merged or deleted.

PR05 candidates are local Git objects, not trusted simply because they have a
hash. Each candidate evaluation still requires explicit local operator approval.
No user branch is merged, and qualification does not consult final data or produce
scientific confirmation. As with PR04, a locally compromised host can rewrite
both the SQLite ledger and CAS; this system does not protect against that threat.
