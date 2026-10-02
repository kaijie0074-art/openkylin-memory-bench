# Read-only Windows hardware inventory. No installation, system changes,
# credential reads, file writes or network uploads. JSON goes to stdout.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$kmbWarnings = [System.Collections.Generic.List[string]]::new()
$kmbProcessors = @()
$kmbRamBytes = $null
$kmbHypervisorPresent = $null
$kmbDisks = @()

try {
    $kmbProcessors = @(Get-CimInstance -ClassName Win32_Processor -Property Name,Architecture,NumberOfCores,NumberOfLogicalProcessors,VirtualizationFirmwareEnabled,VMMonitorModeExtensions,SecondLevelAddressTranslationExtensions | ForEach-Object {
        $kmbArchitecture = switch ([int]$_.Architecture) {
            0 { 'x86' }
            5 { 'ARM' }
            9 { 'x86_64' }
            12 { 'ARM64' }
            default { 'unknown' }
        }
        [ordered]@{
            name = $_.Name
            architecture = $kmbArchitecture
            cores = $_.NumberOfCores
            logical_processors = $_.NumberOfLogicalProcessors
            virtualization_firmware_enabled = $_.VirtualizationFirmwareEnabled
            vm_monitor_extensions = $_.VMMonitorModeExtensions
            second_level_address_translation = $_.SecondLevelAddressTranslationExtensions
        }
    })
} catch {
    $kmbWarnings.Add('CPU and firmware virtualization information unavailable.')
}

try {
    $kmbSystem = Get-CimInstance -ClassName Win32_ComputerSystem -Property TotalPhysicalMemory,HypervisorPresent
    $kmbRamBytes = $kmbSystem.TotalPhysicalMemory
    $kmbHypervisorPresent = $kmbSystem.HypervisorPresent
} catch {
    $kmbWarnings.Add('RAM or hypervisor presence information unavailable.')
}

try {
    $kmbDisks = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType=3' -Property DeviceID,FileSystem,Size,FreeSpace | ForEach-Object {
        [ordered]@{
            drive = $_.DeviceID
            filesystem = $_.FileSystem
            size_bytes = $_.Size
            free_bytes = $_.FreeSpace
        }
    })
} catch {
    $kmbWarnings.Add('Local fixed-disk free space information unavailable.')
}

$kmbRamGiB = if ($null -ne $kmbRamBytes) {
    [math]::Round($kmbRamBytes / 1GB, 2)
} else { $null }

$kmbReport = [ordered]@{
    schema_version = '1'
    purpose = 'openKylin VM feasibility inventory'
    validation = 'read_only_inventory'
    collected_at_utc = [DateTime]::UtcNow.ToString('o')
    process_architecture = $env:PROCESSOR_ARCHITECTURE
    processors = $kmbProcessors
    ram_bytes = $kmbRamBytes
    ram_gib = $kmbRamGiB
    fixed_disks = $kmbDisks
    virtualization = [ordered]@{
        hypervisor_present = $kmbHypervisorPresent
        note = 'Firmware flags are signals only. They do not prove that a VM product is installed or that openKylin boots successfully.'
    }
    warnings = @($kmbWarnings.ToArray())
    uploads_performed = $false
    system_changes_performed = $false
}

$kmbReport | ConvertTo-Json -Depth 8
