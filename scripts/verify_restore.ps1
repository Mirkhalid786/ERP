# Restore a backup into an ISOLATED scratch database and compare row counts with the live database.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\verify_restore.ps1 backups\pharmacy_erp_<stamp>.sql[.gz]
# The scratch database pharmacy_erp_restore_verify is replaced each run. The live database is only read.
param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $File)) { throw "File not found: $File" }
$leaf = Split-Path $File -Leaf
$script = @'
#!/bin/sh
set -e
SCRATCH=pharmacy_erp_restore_verify
LIVE="$MYSQL_DATABASE"
M="mysql -uroot -p$MYSQL_ROOT_PASSWORD"
$M -e "DROP DATABASE IF EXISTS $SCRATCH; CREATE DATABASE $SCRATCH" 2>/dev/null
case "$1" in
  *.gz) gunzip -c "/tmp/$1" | $M "$SCRATCH" 2>/dev/null ;;
  *)    $M "$SCRATCH" < "/tmp/$1" 2>/dev/null ;;
esac
TABLES=$($M -N -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$SCRATCH' AND table_type='BASE TABLE'" 2>/dev/null)
N=0; D=0
for T in $TABLES; do
  N=$((N+1))
  R=$($M -N -e "SELECT (SELECT COUNT(*) FROM $SCRATCH.\`$T\`), (SELECT COUNT(*) FROM $LIVE.\`$T\`)" 2>/dev/null)
  A=$(echo "$R" | cut -f1); B=$(echo "$R" | cut -f2)
  if [ "$A" != "$B" ]; then D=$((D+1)); echo "$T: backup $A / live $B"; fi
done
echo "Restored $N tables into $SCRATCH; $D table(s) differ from live (rows added after the backup also show here)."
rm -f "/tmp/$1"
'@
$tmp = Join-Path $env:TEMP "verify_restore.sh"
[System.IO.File]::WriteAllText($tmp, ($script -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding $false))
docker cp (Resolve-Path $File).Path "pharmacy_db:/tmp/$leaf" | Out-Null
docker cp $tmp "pharmacy_db:/tmp/verify_restore.sh" | Out-Null
docker exec pharmacy_db sh /tmp/verify_restore.sh $leaf
$code = $LASTEXITCODE
docker exec pharmacy_db rm -f /tmp/verify_restore.sh
Remove-Item $tmp -Confirm:$false
if ($code -ne 0) { throw "Restore verification failed (exit $code)" }
