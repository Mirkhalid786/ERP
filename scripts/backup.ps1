# Back up the Pharmacy ERP database with mysqldump inside the database container.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
# Writes backups\pharmacy_erp_<yyyyMMdd_HHmmss>.sql (byte-for-byte as mysqldump produced it) and prints its SHA-256.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root "backups"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$name = "pharmacy_erp_{0}.sql" -f (Get-Date -Format "yyyyMMdd_HHmmss")
$file = Join-Path $dir $name
# Dump to a file inside the container, then copy it out unchanged (no PowerShell re-encoding)
docker exec pharmacy_db sh -c "mysqldump -u`"`$MYSQL_USER`" -p`"`$MYSQL_PASSWORD`" --single-transaction --no-tablespaces `"`$MYSQL_DATABASE`" > /tmp/$name 2>/dev/null"
if ($LASTEXITCODE -ne 0) { throw "mysqldump failed" }
docker cp "pharmacy_db:/tmp/$name" $file | Out-Null
docker exec pharmacy_db rm -f "/tmp/$name"
$tail = Get-Content $file -Tail 1
if ($tail -notmatch "Dump completed") { throw "Backup incomplete: $file" }
$hash = (Get-FileHash $file -Algorithm SHA256).Hash
Write-Output ("Backup OK: {0}  {1:N0} bytes  SHA256 {2}" -f $file, (Get-Item $file).Length, $hash)
