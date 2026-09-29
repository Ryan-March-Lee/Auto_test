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
The registered smoke test sends only configured, allow-listed query commands
(`*IDN?`, `SYST:ERR?`, and `OUTP?`) and closes every opened resource; it does
not enable RF or power outputs. The example configuration shows the expected
identity, error-state, and output-state checks for each device.
