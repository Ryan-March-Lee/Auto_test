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
analyzer, and power supply. For `minimal_action`, the power supply may use
`discover: true` without an address: the runner enumerates VISA resources,
queries `*IDN?` and both configured output states, and selects exactly one
candidate whose channels are off. A configured model remains an optional
identity filter; ambiguity or no safe match fails before RF is enabled. Signal
generator and supply configurations must query their output state before and
after cleanup with an explicit expected-off value. The analyzer must provide
an explicit post-setup state query. Setup and cleanup SCPI roots are restricted
per device; confirm those command forms and limits against the actual
instrument manuals before site use. Cleanup commands are attempted
independently, and any cleanup or final-state verification failure fails the
smoke and is retained in the local report.

Current site status: `safe_prepare`, the formal `read_only` entry, and one
`minimal_action` run have passed with the local configuration. The minimal
action used 2.3 GHz and -30 dBm, performed one bounded RF action and analyzer
measurement, then confirmed RF off, all discovered power-supply channels off,
and closed VISA resources. The local report remains untracked. Re-run the
same gate only when the hardware smoke implementation or application assembly
path changes, and record a fresh report before claiming the changed path is
validated. A front-panel remote-control indicator may remain after VISA
cleanup; use the instrument's documented Local operation and check for other
VISA clients before treating that indicator as a connection leak.
