# Security Policy

## Supported Versions

This project is pre-1.0 and security fixes are applied to `master`.

## Reporting a Vulnerability

Please do not open public issues for security vulnerabilities.

Report privately to the maintainer with:
- vulnerability description
- impact
- proof of concept (if available)
- suggested fix (optional)

The project target response window is 72 hours for acknowledgment.

## Threat Model Notes

This bridge handles SMS content and should be treated as an untrusted-input boundary.

Key risks:
- social engineering/prompt injection via inbound SMS
- outbound abuse/spam if host or outbox is compromised
- privacy leakage through file/log permissions

Minimum production baseline:
- sender allowlist (`JMP_ALLOWED_SENDERS`)
- outbound rate limiting
- strict filesystem permissions
- local hook targets + auth token
