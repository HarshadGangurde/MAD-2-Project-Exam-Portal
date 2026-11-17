# EMP Submission Package Preparation Script
# Run this after filling REPORT.md with your details

param(
    [Parameter(Mandatory=$true)]
    [string]$RollNumber
)

$ErrorActionPreference = "Stop"

Write-Host "🎓 Preparing EMP Submission Package for Roll Number: $RollNumber" -ForegroundColor Cyan

# Set submission folder name
$submissionFolder = "emp_2xf$RollNumber"
$submissionPath = "c:\MAD 2\$submissionFolder"

# Remove existing submission folder if it exists
if (Test-Path $submissionPath) {
    Write-Host "⚠️  Removing existing submission folder..." -ForegroundColor Yellow
    Remove-Item -Path $submissionPath -Recurse -Force
}

# Create fresh submission folder
Write-Host "📁 Creating submission folder: $submissionFolder" -ForegroundColor Green
New-Item -ItemType Directory -Path $submissionPath | Out-Null

# Copy required folders
Write-Host "📂 Copying project folders..." -ForegroundColor Green
$folders = @('backend', 'static', 'templates')
foreach ($folder in $folders) {
    $sourcePath = "c:\MAD 2\emp\$folder"
    if (Test-Path $sourcePath) {
        Copy-Item -Path $sourcePath -Destination $submissionPath -Recurse -Force
        Write-Host "   ✓ Copied $folder" -ForegroundColor Gray
    }
}

# Copy required root files
Write-Host "📄 Copying project files..." -ForegroundColor Green
$files = @('README.md', 'REPORT.md', 'requirements.txt', 'openapi.yaml', 'run.py', 'seed_data.py', 'check_services.py')
foreach ($file in $files) {
    $sourcePath = "c:\MAD 2\emp\$file"
    if (Test-Path $sourcePath) {
        Copy-Item -Path $sourcePath -Destination $submissionPath -Force
        Write-Host "   ✓ Copied $file" -ForegroundColor Gray
    }
}

# Remove __pycache__ folders
Write-Host "🧹 Cleaning up __pycache__ folders..." -ForegroundColor Green
Get-ChildItem -Path $submissionPath -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force

# Remove .pyc files
Write-Host "🧹 Cleaning up .pyc files..." -ForegroundColor Green
Get-ChildItem -Path $submissionPath -Recurse -Filter "*.pyc" | Remove-Item -Force

# Create ZIP file
$zipPath = "c:\MAD 2\$submissionFolder.zip"
if (Test-Path $zipPath) {
    Remove-Item -Path $zipPath -Force
}

Write-Host "📦 Creating ZIP archive..." -ForegroundColor Green
Compress-Archive -Path $submissionPath -DestinationPath $zipPath -Force

# Calculate ZIP size
$zipSize = (Get-Item $zipPath).Length / 1MB
Write-Host "✅ Submission package created successfully!" -ForegroundColor Green
Write-Host "   📦 Location: $zipPath" -ForegroundColor Cyan
Write-Host "   💾 Size: $([math]::Round($zipSize, 2)) MB" -ForegroundColor Cyan

Write-Host "`n📋 Submission Checklist:" -ForegroundColor Yellow
Write-Host "   ☐ REPORT.md filled with your details (name, roll, email)" -ForegroundColor White
Write-Host "   ☐ AI/LLM declaration completed in REPORT.md" -ForegroundColor White
Write-Host "   ☐ ER diagram exported as image from mermaid.live" -ForegroundColor White
Write-Host "   ☐ Demo video recorded and uploaded to Google Drive" -ForegroundColor White
Write-Host "   ☐ Video link added to REPORT.md section 8" -ForegroundColor White
Write-Host "   ☐ REPORT.md converted to PDF (if required)" -ForegroundColor White

Write-Host "`n🎯 Next Steps:" -ForegroundColor Yellow
Write-Host "   1. Complete all checklist items above" -ForegroundColor White
Write-Host "   2. Extract and verify: $zipPath" -ForegroundColor White
Write-Host "   3. Submit the ZIP file to your course portal" -ForegroundColor White
