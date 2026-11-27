# Mirror the IssuePilot audit ledger in Snowflake

The app runs without Snowflake. This optional integration copies **event metadata** from PostgreSQL and checks the IDs, content and hashes against the remote table. It does not send ticket bodies, generated comments, secrets or tokens. Start the workspace and receive at least one issue before trying it.

| Step | Where | Identity / role | Warehouse, database and schema |
|---|---|---|---|
| Create resources | New SQL file in Snowsight | Human with SYSADMIN or the existing object owner | No current database is needed; the SQL qualifies names |
| Create role, service user and grants | Same SQL file | Human with SECURITYADMIN or delegated grant manager | Use account that owns the target resources |
| Check access | Snowsight | Same grant administrator | Select a warehouse the human can use for the joined grant report |
| Check and sync | PowerShell, repo root | Key-pair service login `ISSUEPILOT_SVC`, role `ISSUEPILOT_SYNC` | `ISSUEPILOT_WH` · `ISSUEPILOT_DB.ANALYTICS` |
| Read reports | Snowsight | Human owner/authorized reporting role with SELECT and USAGE | Select `ISSUEPILOT_WH` or another permitted running warehouse; the standard report names the table in full |

1. **Provision the destination.** In the correct account, run [setup.sql](../warehouse/setup.sql) in order. The first block creates or retains the warehouse, database, schema and table; the second creates or retains the service identity and grants access. The script does not replace existing objects or keys. On accounts with custom ownership, ask the actual owner/grant administrator to run its part. Keep administrative roles off the service account.

2. **Verify the grants.** Run [check_access.sql](../warehouse/check_access.sql) as a single statement in Snowsight. Expect **six PASS rows**: role to user, warehouse USAGE, database USAGE, schema USAGE, table SELECT and INSERT. Fix each MISSING row using the corresponding GRANT from setup. Seeing a role name in the editor or a public-key fingerprint on a user is not proof of warehouse access.

3. **Configure the key and connection.** Reuse a working key if you already have one. For a new service user, create an encrypted RSA key in PowerShell (OpenSSL on PATH or installed with Git for Windows):

```powershell
$priv = '.\issuepilot\secrets\snowflake.p8'
New-Item -ItemType Directory -Force (Split-Path $priv) | Out-Null
$ssl = (Get-Command openssl.exe -ErrorAction SilentlyContinue).Source
if (-not $ssl) { $ssl = Join-Path $env:ProgramFiles 'Git\usr\bin\openssl.exe' }
if (-not (Test-Path -LiteralPath $ssl -PathType Leaf)) { throw 'Install OpenSSL first.' }
if (-not (Test-Path -LiteralPath $priv)) {
    & $ssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -aes-256-cbc -out $priv
    if ($LASTEXITCODE -ne 0) { throw 'Private-key generation failed.' }
}
$pem = & $ssl pkey -in $priv -pubout
if ($LASTEXITCODE -ne 0) { throw 'Could not read the public key.' }
$pub = ($pem | Where-Object { $_ -and $_ -notmatch '^-----' }) -join ''
if (-not $pub) { throw 'No public key returned.' }
"ALTER USER ISSUEPILOT_SVC SET RSA_PUBLIC_KEY='$pub';" | Set-Clipboard
```

For a new user only, paste the public-key statement into Snowsight as the service user's owner. Do not overwrite an existing registration just to diagnose permissions. The private key stays at the path above and must be readable inside the analytics container. In Snowsight, obtain the account identifier with:

```sql
SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME() AS connector_account;
```

On Windows, after `manage.py start`, open the **active** settings file. A source checkout only includes `.env.example`; the launcher keeps credentials outside Git. Set the two lines below once in that file using your actual connector account.

```powershell
notepad (py -3.12 .\manage.py settings-path)
```

```dotenv
IP_SF_ACCOUNT=your-organization-your-account
IP_SF_USER=ISSUEPILOT_SVC
```

The role, warehouse, database and schema default to the names in the table. Process environment variables override the file. If you previously set an incorrect account/user in this terminal, remove just those overrides with `Remove-Item Env:IP_SF_ACCOUNT,Env:IP_SF_USER -ErrorAction SilentlyContinue`.

4. **Verify then sync.** From the repo root, in the same PowerShell window:

```powershell
$keyPass = Read-Host 'Private key passphrase' -AsSecureString
$env:IP_SF_PRIVATE_KEY_PASSWORD = [System.Net.NetworkCredential]::new('', $keyPass).Password
try {
    py -3.12 .\manage.py check-snowflake
    if ($LASTEXITCODE -ne 0) { throw 'Context check failed. Inspect the named object or grant.' }
    py -3.12 .\manage.py sync
    if ($LASTEXITCODE -ne 0) { throw 'Sync did not finish. Do not report this as verified.' }
} finally {
    Remove-Item Env:IP_SF_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
}
```

`CONTEXT_OK` checks the service session and table SELECT. `VERIFIED` means the merge and full readback passed. A second sync with unchanged source events should have `inserted: 0`; a running worker may legitimately add new events. No shell from a previous session is required. `manage.py export` is a local-only alternative.

5. **Query the mirror.** In Snowsight choose a human role with read access and a usable warehouse. Run either SELECT from [intake_metrics.sql](../warehouse/intake_metrics.sql). Each lists workspaces and recognizes both historical and current ID fields. Locate yours using `manage.py status`. A NULL source size is expected for older receipts without it. Cases ever failed can later complete; proposals and reviews are not accuracy measures. [metrics.sql](../warehouse/metrics.sql) reports global events. No domain name, personal role, or session variable belongs in the report.

Snowflake references: [key-pair authentication](https://docs.snowflake.com/en/user-guide/key-pair-auth), [access control](https://docs.snowflake.com/en/user-guide/security-access-control-overview), [pipe operator used by the grant check](https://docs.snowflake.com/en/sql-reference/operators-flow).
