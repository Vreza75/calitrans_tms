# Claude Code environment setup for this repo.
# Installs plugin marketplaces/plugins, LSP servers, and MCP servers used in this project.
# Run from the repo root: .\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "== Claude plugin marketplaces ==" -ForegroundColor Cyan
claude plugin marketplace add anthropics/claude-plugins-official
claude plugin marketplace add upstash/context7

Write-Host "== Claude plugins ==" -ForegroundColor Cyan
claude plugin install claude-code-setup@claude-plugins-official
claude plugin install feature-dev@claude-plugins-official
claude plugin install frontend-design@claude-plugins-official
claude plugin install pyright-lsp@claude-plugins-official
claude plugin install typescript-lsp@claude-plugins-official
claude plugin install supabase@claude-plugins-official
claude plugin install context7@context7-marketplace

Write-Host "== Language servers ==" -ForegroundColor Cyan
py -m pip install pyright
npm install -g typescript-language-server typescript

Write-Host "== MCP servers ==" -ForegroundColor Cyan
claude mcp add playwright npx @playwright/mcp@latest

Write-Host "Setup complete." -ForegroundColor Green
