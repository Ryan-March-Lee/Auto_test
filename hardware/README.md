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
The preparation runner requires exactly one signal generator and spectrum
analyzer. The power-supply section may use `discover: true` without an
address: it enumerates VISA resources, queries `*IDN?` and both output states,
and includes every matching idle DP832A. The operator must ensure all supplies
are unloaded before starting. Each discovered supply is connected, included in
the action/cleanup boundary, and verified off; the number and addresses may
differ between tests. The application hardware factory is separate from this
smoke behavior: it opens only configured power-supply addresses and routes each
logical role to its configured supply and channel. Measurement assignments
define the production topology; smoke discovery must not be used as that
topology.
Signal-generator and supply configurations must query output state before and
after cleanup. Cleanup failures fail the smoke and remain in the local report.

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
