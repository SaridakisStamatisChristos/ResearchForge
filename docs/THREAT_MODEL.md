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

## PR06 final exposure and audit boundaries

Candidate selection is irrevocably closed before final evaluator exposure. No
direct CLI option gives a candidate access to the final split during development.
Final-subprocess execution still operates with ordinary trusted-local host
permissions; this is **not** a sandbox or a defense against a malicious trusted
command that reads final data through the host filesystem. The final event chain
is audit-detectable but not administrator-tamper-proof. Interruption after final
start invalidates the one-shot evaluation rather than authorizing a silent rerun.
The scalar final threshold cannot establish generalization or statistical
significance; bootstrap routines require authentic, correctly paired case-level
observations and honest sampling-unit assumptions.
