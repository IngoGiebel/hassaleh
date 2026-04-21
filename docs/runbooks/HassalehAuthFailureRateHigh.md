# Runbook: HassalehAuthFailureRateHigh

## Symptom
Authentication failures exceed 1/s for 1 min.

## Likely Causes
- Credential stuffing attack or brute force.
- Misconfigured agent using invalid keys.

## Panels to Check
- **Hassaleh Auth Security** (`ha-auth-v1`): "Auth Attempts" panel.

## Immediate Actions
1. Check source IP or agent ID in Loki.
2. If malicious, block IP.
3. If internal, rotate credentials.
