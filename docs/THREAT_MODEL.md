# Threat model: PR01 scope

Untrusted manifest data must not execute commands. The schema CLI only produces text.
Unknown JSON fields are rejected, and nonfinite results are rejected. Pydantic validation
is not isolation, data integrity or authentication. Future trusted-local evaluators will
require explicit approval and must not be described as adversarial-code sandboxes.
