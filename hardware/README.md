# Hardware smoke

Hardware smoke is deliberately outside the default `tests/` discovery path.
The local configuration must be an untracked file such as
`hardware/smoke_config.local.json`; never put real addresses or credentials in
the example file.

The only recommended entry point is:

```powershell
$env:HARDWARE_SMOKE_ENABLED = "1"
./run_hardware_smoke.ps1 -ConfigPath "hardware/smoke_config.local.json" -ConfirmHardwareSmoke
```

The entry point requires the interpreter from `.env`, a non-example
`hardware_smoke` configuration, explicit confirmation, and a non-CI process.
The preparation runner requires exactly one signal generator, spectrum
analyzer, and power supply. Signal generator and supply configurations must
query their output state before and after cleanup with an explicit expected-off
value. The analyzer must provide an explicit post-setup state query. Setup and
cleanup SCPI roots are restricted per device; confirm those command forms and
limits against the actual instrument manuals before site use. Cleanup commands
are attempted independently, and any cleanup or final-state verification
failure fails the smoke and is retained in the local report.

Current site status: `safe_prepare` and the formal `read_only` entry have
passed with the local configuration. The runs confirmed matching identities,
RF off, both power-supply channels off, and closed VISA resources.
`minimal_action` was interrupted before `OUTP ON`; no real RF action or
measurement was performed. The offline validation is now complete, including
the `CALC:MARK1:MAX` -> `CALC:MARK1:Y?` sequence, measurement bounds, action
budget, timeout, and RF/power cleanup. The untracked local configuration is
prepared for `minimal_action` at 2.3 GHz and -30 dBm. Before executing it,
confirm the equipment is unloaded and obtain explicit authorization for one
bounded RF action. A front-panel remote-control indicator may remain after
VISA cleanup; use the instrument's documented Local operation and check for
other VISA clients before treating that indicator as a connection leak.
