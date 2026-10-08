#!/usr/bin/env python3
"""
GitHub OAuth Login UI Server
============================
A zero-dependency local web UI & OAuth portal to authenticate with GitHub,
obtain an OAuth Access Token, inspect user profile and permissions,
and automatically save GITHUB_TOKEN to .env.

Usage:
    python3 github_oauth_ui.py [--port PORT] [--no-browser]
"""

import sys
import os
import json
import time
import secrets
import argparse
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

def load_env_file(path: str = ENV_FILE_PATH) -> Dict[str, str]:
    env_vars = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip().strip("'\"")
    return env_vars

def save_token_to_env(token: str, path: str = ENV_FILE_PATH) -> bool:
    token = token.strip()
    try:
        lines = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        
        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("GITHUB_TOKEN=") or (stripped.startswith("GITHUB_TOKEN ") and "=" in stripped):
                new_lines.append(f"GITHUB_TOKEN={token}\n")
                found = True
            else:
                new_lines.append(line)
        
        if not found:
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            new_lines.append(f"GITHUB_TOKEN={token}\n")
            
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        os.environ["GITHUB_TOKEN"] = token
        return True
    except Exception as e:
        print(f"[-] Error saving token to .env: {e}")
        return False

def make_http_request(url: str, method: str = "GET", headers: Optional[Dict[str, str]] = None, body: Optional[dict] = None) -> tuple[int, Dict[str, str], Any]:
    """Helper to perform HTTP requests using standard urllib."""
    hdrs = {
        "User-Agent": "GitHub-OAuth-Portal-Local/1.0",
        "Accept": "application/json",
    }
    if headers:
        hdrs.update(headers)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read().decode("utf-8")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return resp.status, resp_headers, parsed
    except urllib.error.HTTPError as e:
        resp_headers = {k.lower(): v for k, v in e.headers.items()}
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"error": "http_error", "message": raw}
        return e.code, resp_headers, parsed
    except Exception as e:
        return 500, {}, {"error": "exception", "message": str(e)}

# Global State Container
class AppState:
    def __init__(self):
        self.port = 8000
        self.client_id = ""
        self.client_secret = ""
        self.current_token = ""
        self.user_profile = None
        self.rate_limit = None
        self.token_scopes = []
        self.last_error = None
        self.active_states = set()
        self.device_sessions = {}
        self.reload_config()

    def reload_config(self):
        env = load_env_file()
        self.client_id = env.get("GITHUB_CLIENT_ID", "").strip()
        self.client_secret = env.get("GITHUB_CLIENT_SECRET", "").strip()
        stored_token = env.get("GITHUB_TOKEN", "").strip()
        if stored_token and not self.current_token:
            self.current_token = stored_token
            self.fetch_user_profile(stored_token)

    def fetch_user_profile(self, token: str) -> Optional[dict]:
        status, headers, data = make_http_request(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if status == 200 and isinstance(data, dict):
            self.user_profile = data
            scopes_header = headers.get("x-oauth-scopes", "")
            self.token_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
            self.fetch_rate_limit(token)
            return data
        else:
            self.user_profile = None
            return None

    def fetch_rate_limit(self, token: str) -> Optional[dict]:
        status, _, data = make_http_request(
            "https://api.github.com/rate_limit",
            headers={
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if status == 200 and isinstance(data, dict):
            self.rate_limit = data.get("resources", {}).get("core", {})
            return self.rate_limit
        return None

app_state = AppState()

# HTML + CSS + JS Frontend
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitHub OAuth Portal & Token Manager</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            github: {
              dark: '#0d1117',
              surface: '#161b22',
              card: '#1f242c',
              border: '#30363d',
              accent: '#238636',
              accentHover: '#2ea043',
              blue: '#58a6ff',
              purple: '#bc8cff',
              muted: '#8b949e',
              danger: '#f85149'
            }
          }
        }
      }
    }
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; }
    code, pre, .mono { font-family: 'JetBrains Mono', monospace; }
    .glass { background: rgba(22, 27, 34, 0.85); backdrop-filter: blur(12px); }
    .pulse-glow { box-shadow: 0 0 25px rgba(35, 134, 54, 0.35); }
    .pulse-blue { box-shadow: 0 0 25px rgba(88, 166, 255, 0.3); }
    [v-cloak] { display: none; }
  </style>
</head>
<body class="bg-[#090d13] text-gray-100 min-h-screen flex flex-col items-center justify-start p-4 sm:p-8">

  <!-- Toast Notification -->
  <div id="toast" class="fixed top-5 right-5 z-50 transform transition-all duration-300 translate-y-[-100px] opacity-0 pointer-events-none flex items-center gap-3 px-5 py-3 rounded-xl border shadow-2xl bg-github-surface border-github-border text-sm">
    <span id="toastIcon"></span>
    <span id="toastMsg" class="font-medium"></span>
  </div>

  <div class="w-full max-w-4xl space-y-6">

    <!-- Top Navigation / Header -->
    <header class="glass border border-github-border rounded-2xl p-6 flex flex-col md:flex-row items-center justify-between gap-4 shadow-xl">
      <div class="flex items-center gap-4">
        <div class="w-12 h-12 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center p-2 text-white">
          <svg class="w-8 h-8 fill-current" viewBox="0 0 24 24">
            <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
          </svg>
        </div>
        <div>
          <h1 class="text-xl sm:text-2xl font-bold tracking-tight text-white flex items-center gap-2">
            GitHub OAuth Portal
            <span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-github-blue/15 text-github-blue border border-github-blue/30">Local Auth</span>
          </h1>
          <p class="text-xs sm:text-sm text-github-muted">Đăng nhập GitHub & tự động xuất OAuth Access Token vào <code class="text-gray-300">.env</code></p>
        </div>
      </div>

      <!-- Env Status Badges -->
      <div class="flex flex-wrap items-center gap-2 text-xs">
        <div id="clientIdBadge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-github-surface border-github-border">
          <span class="w-2 h-2 rounded-full bg-yellow-400" id="clientIdDot"></span>
          <span class="text-github-muted">Client ID:</span>
          <span class="font-mono text-gray-200" id="clientIdText">Checking...</span>
        </div>
        <div id="secretBadge" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border bg-github-surface border-github-border">
          <span class="w-2 h-2 rounded-full bg-yellow-400" id="secretDot"></span>
          <span class="text-github-muted">Client Secret:</span>
          <span class="font-mono text-gray-200" id="secretText">Checking...</span>
        </div>
      </div>
    </header>

    <!-- Error Banner (if any) -->
    <div id="errorBanner" class="hidden border border-github-danger/40 bg-github-danger/10 text-github-danger rounded-xl p-4 text-sm flex items-start gap-3">
      <svg class="w-5 h-5 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
      </svg>
      <div class="space-y-1">
        <div class="font-bold" id="errorTitle">Lỗi xác thực</div>
        <div id="errorMessage" class="text-xs text-red-200/90 leading-relaxed"></div>
      </div>
    </div>

    <!-- Active Token / Authenticated User Card -->
    <div id="authSection" class="hidden glass border border-github-border rounded-2xl p-6 sm:p-8 space-y-6 shadow-2xl relative overflow-hidden">
      <div class="absolute -top-24 -right-24 w-60 h-60 bg-github-accent/15 rounded-full blur-3xl pointer-events-none"></div>

      <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-github-border pb-6">
        <!-- User Info -->
        <div class="flex items-center gap-4">
          <img id="userAvatar" src="" alt="Avatar" class="w-16 h-16 rounded-full border-2 border-github-accent p-0.5 shadow-md">
          <div>
            <div class="flex items-center gap-2">
              <span id="userName" class="text-lg font-bold text-white"></span>
              <span id="userLogin" class="text-sm font-mono text-github-blue"></span>
            </div>
            <p id="userBio" class="text-xs text-github-muted max-w-md mt-0.5 line-clamp-1"></p>
            <div class="flex items-center gap-4 text-xs text-github-muted mt-2">
              <span id="userEmail" class="flex items-center gap-1"></span>
              <span id="userRepos" class="flex items-center gap-1"></span>
            </div>
          </div>
        </div>

        <!-- Quick actions -->
        <div class="flex items-center gap-2 self-end sm:self-center">
          <button onclick="reAuthenticate()" class="px-3 py-1.5 rounded-lg border border-github-border hover:bg-white/5 text-xs text-gray-300 font-medium transition flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
            Re-login
          </button>
          <button onclick="logout()" class="px-3 py-1.5 rounded-lg border border-red-500/30 hover:bg-red-500/10 text-xs text-red-400 font-medium transition flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
            Sign out
          </button>
        </div>
      </div>

      <!-- Token Box -->
      <div class="space-y-3">
        <div class="flex items-center justify-between text-xs">
          <span class="font-semibold text-gray-300 flex items-center gap-1.5">
            <svg class="w-4 h-4 text-github-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/></svg>
            GitHub Access Token
          </span>
          <span id="envSavedStatus" class="flex items-center gap-1 text-emerald-400 font-medium">
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
            Đã đồng bộ vào .env (GITHUB_TOKEN)
          </span>
        </div>

        <div class="flex items-center gap-2">
          <div class="relative flex-1">
            <input id="tokenInput" type="password" readonly class="w-full bg-[#0d1117] border border-github-border rounded-xl px-4 py-2.5 text-sm font-mono text-emerald-300 focus:outline-none focus:border-github-blue pr-20 select-all shadow-inner">
            <button onclick="toggleTokenVisibility()" class="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-github-muted hover:text-white transition px-1.5 py-0.5 rounded">
              <span id="toggleTokenText">Hiện</span>
            </button>
          </div>
          <button onclick="copyToken()" class="px-5 py-2.5 bg-github-accent hover:bg-github-accentHover text-white font-semibold text-sm rounded-xl transition flex items-center gap-2 shadow-lg pulse-glow">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/></svg>
            Copy Token
          </button>
          <button onclick="saveTokenToEnv()" id="saveBtn" class="px-4 py-2.5 bg-white/5 hover:bg-white/10 border border-github-border text-gray-200 font-medium text-sm rounded-xl transition flex items-center gap-2">
            <svg class="w-4 h-4 text-github-blue" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-3m-1 4l-3 3m0 0l-3-3m3 3V4"/></svg>
            Lưu vào .env
          </button>
        </div>
      </div>

      <!-- Token Scopes & Rate Limit Stats -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
        <!-- Scopes -->
        <div class="bg-github-surface/60 border border-github-border rounded-xl p-4 space-y-2">
          <div class="text-xs font-semibold text-gray-300 flex items-center justify-between">
            <span>Scopes được cấp phép:</span>
            <span id="scopeCount" class="font-mono text-github-blue text-[11px]">0 scopes</span>
          </div>
          <div id="grantedScopesList" class="flex flex-wrap gap-1.5 pt-1">
            <!-- Dynamic tags -->
          </div>
        </div>

        <!-- Rate Limit -->
        <div class="bg-github-surface/60 border border-github-border rounded-xl p-4 space-y-2">
          <div class="text-xs font-semibold text-gray-300 flex items-center justify-between">
            <span>API Rate Limit (Core):</span>
            <span id="rateLimitText" class="font-mono text-github-accent text-[11px]">-- / --</span>
          </div>
          <div class="w-full bg-white/5 rounded-full h-2 overflow-hidden border border-white/5">
            <div id="rateLimitBar" class="bg-github-accent h-full transition-all duration-500" style="width: 100%"></div>
          </div>
          <p class="text-[11px] text-github-muted" id="rateLimitReset">Reset lúc: --</p>
        </div>
      </div>

      <!-- Quick Test & Tools -->
      <div class="border-t border-github-border pt-4">
        <div class="flex items-center justify-between mb-3">
          <span class="text-xs font-bold uppercase tracking-wider text-github-muted">Kiểm tra API trực tiếp</span>
          <div class="flex items-center gap-2">
            <button onclick="testApi('/user')" class="px-2.5 py-1 text-xs rounded-lg bg-white/5 hover:bg-white/10 border border-github-border text-gray-300">GET /user</button>
            <button onclick="testApi('/user/repos?per_page=5&sort=updated')" class="px-2.5 py-1 text-xs rounded-lg bg-white/5 hover:bg-white/10 border border-github-border text-gray-300">GET /user/repos</button>
            <button onclick="testApi('/rate_limit')" class="px-2.5 py-1 text-xs rounded-lg bg-white/5 hover:bg-white/10 border border-github-border text-gray-300">GET /rate_limit</button>
          </div>
        </div>
        <pre id="apiTestOutput" class="hidden bg-[#0d1117] border border-github-border rounded-xl p-4 text-xs font-mono text-emerald-400 max-h-56 overflow-y-auto"></pre>
      </div>
    </div>

    <!-- Login Options Card (When NOT logged in or requesting re-login) -->
    <div id="loginSection" class="glass border border-github-border rounded-2xl p-6 sm:p-8 space-y-6 shadow-2xl">

      <!-- Navigation Tabs -->
      <div class="flex items-center gap-3 border-b border-github-border pb-4">
        <button id="tabWebBtn" onclick="switchTab('web')" class="px-4 py-2 rounded-xl text-sm font-semibold transition bg-github-blue/15 text-github-blue border border-github-blue/30">
          1. OAuth Web Flow (Khuyến nghị)
        </button>
        <button id="tabDeviceBtn" onclick="switchTab('device')" class="px-4 py-2 rounded-xl text-sm font-semibold transition text-github-muted hover:text-white border border-transparent">
          2. Device Code Flow (Không cần Redirect URL)
        </button>
      </div>

      <!-- TAB 1: OAuth Web Flow -->
      <div id="tabWebContent" class="space-y-6">
        <div class="space-y-3">
          <h2 class="text-base font-bold text-white flex items-center gap-2">
            Cấu hình quyền truy cập (Scopes)
            <span class="text-xs font-normal text-github-muted">Chọn các quyền bạn cần cho token:</span>
          </h2>

          <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 text-xs">
            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="repo" checked class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">repo</div>
                <div class="text-[11px] text-github-muted">Toàn quyền truy cập private & public repos (commit, prs, issues)</div>
              </div>
            </label>

            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="read:user" checked class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">read:user</div>
                <div class="text-[11px] text-github-muted">Xem thông tin cá nhân, profile và username</div>
              </div>
            </label>

            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="user:email" checked class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">user:email</div>
                <div class="text-[11px] text-github-muted">Xem email chính và các email phụ GitHub</div>
              </div>
            </label>

            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="read:org" class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">read:org</div>
                <div class="text-[11px] text-github-muted">Đọc danh sách tổ chức & team membership</div>
              </div>
            </label>

            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="workflow" class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">workflow</div>
                <div class="text-[11px] text-github-muted">Cập nhật workflow GitHub Actions</div>
              </div>
            </label>

            <label class="flex items-start gap-2.5 p-3 rounded-xl border border-github-border bg-github-surface/70 hover:border-github-blue/50 cursor-pointer transition">
              <input type="checkbox" value="gist" class="scope-checkbox mt-0.5 rounded text-github-accent focus:ring-0">
              <div>
                <div class="font-semibold text-white">gist</div>
                <div class="text-[11px] text-github-muted">Quản lý các Gist cá nhân</div>
              </div>
            </label>
          </div>
        </div>

        <!-- Callback URL Notice -->
        <div class="p-4 rounded-xl border border-github-blue/30 bg-github-blue/5 space-y-2 text-xs">
          <div class="flex items-center justify-between">
            <span class="font-semibold text-github-blue flex items-center gap-1.5">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              GitHub OAuth App Callback URL:
            </span>
            <button onclick="copyCallbackUrl()" class="text-xs text-github-blue hover:underline">Copy URL</button>
          </div>
          <div class="flex items-center gap-2">
            <code id="callbackUrlDisplay" class="font-mono bg-[#0d1117] px-3 py-1.5 rounded-lg border border-github-border text-gray-200 flex-1">http://localhost:8000/callback</code>
          </div>
          <p class="text-github-muted text-[11px]">
            Đảm bảo trong cài đặt GitHub OAuth App của bạn (Developer settings -> OAuth Apps), mục <strong>Authorization callback URL</strong> được cài là địa chỉ trên.
          </p>
        </div>

        <!-- Big Sign in Button -->
        <div>
          <button onclick="startWebLogin()" class="w-full py-3.5 px-6 rounded-xl bg-github-accent hover:bg-github-accentHover text-white font-bold text-base transition flex items-center justify-center gap-3 shadow-xl pulse-glow group">
            <svg class="w-6 h-6 fill-current transition transform group-hover:scale-110" viewBox="0 0 24 24">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
            </svg>
            <span>Đăng nhập với GitHub</span>
          </button>
        </div>
      </div>

      <!-- TAB 2: Device Code Flow -->
      <div id="tabDeviceContent" class="hidden space-y-6">
        <div class="text-xs text-github-muted leading-relaxed">
          Device Flow cho phép bạn đăng nhập mà không cần cấu hình Callback URL hoặc mở port. Bạn sẽ nhận được 1 mã code gồm 8 ký tự và dán vào trang <a href="https://github.com/login/device" target="_blank" class="text-github-blue underline">github.com/login/device</a>.
        </div>

        <div id="deviceStartBox">
          <button onclick="startDeviceLogin()" class="w-full py-3 px-6 rounded-xl bg-github-blue/20 hover:bg-github-blue/30 border border-github-blue/40 text-github-blue font-bold text-sm transition flex items-center justify-center gap-2">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
            Tạo mã Device Code
          </button>
        </div>

        <div id="deviceActiveBox" class="hidden space-y-5 p-6 rounded-2xl border border-github-border bg-github-surface/80">
          <div class="text-center space-y-2">
            <span class="text-xs font-semibold uppercase tracking-wider text-github-muted">Mã xác thực của bạn:</span>
            <div class="flex items-center justify-center gap-3">
              <span id="deviceUserCode" class="font-mono text-3xl font-extrabold text-white tracking-widest px-4 py-2 rounded-xl bg-[#0d1117] border border-github-border select-all">----</span>
              <button onclick="copyDeviceCode()" class="p-3 bg-white/5 hover:bg-white/10 border border-github-border rounded-xl text-gray-200">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"/></svg>
              </button>
            </div>
          </div>

          <div class="flex flex-col sm:flex-row items-center justify-center gap-3">
            <a id="deviceVerifyLink" href="https://github.com/login/device" target="_blank" class="w-full sm:w-auto px-6 py-2.5 bg-github-accent hover:bg-github-accentHover text-white font-semibold text-sm rounded-xl transition flex items-center justify-center gap-2">
              <span>Mở trang GitHub Device</span>
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"/></svg>
            </a>
          </div>

          <div class="flex items-center justify-center gap-2 text-xs text-yellow-400">
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span id="devicePollStatus">Đang chờ bạn nhập mã và duyệt trên GitHub...</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Quick instructions footer -->
    <footer class="text-center text-xs text-github-muted space-y-1">
      <p>Sau khi đăng nhập thành công, token được lưu trực tiếp vào biến <code class="text-gray-300">GITHUB_TOKEN</code> trong file <code class="text-gray-300">.env</code>.</p>
      <p>Bạn có thể dùng ngay với script <code class="text-gray-300">github_activity_crawler.py</code> hoặc các tool khác.</p>
    </footer>

  </div>

  <script>
    let currentConfig = {};
    let isTokenVisible = false;
    let devicePollIntervalId = null;

    // Toast helper
    function showToast(message, type = 'success') {
      const toast = document.getElementById('toast');
      const toastMsg = document.getElementById('toastMsg');
      const toastIcon = document.getElementById('toastIcon');
      
      toastMsg.innerText = message;
      if (type === 'success') {
        toastIcon.innerHTML = `<svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>`;
      } else {
        toastIcon.innerHTML = `<svg class="w-5 h-5 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>`;
      }
      
      toast.classList.remove('translate-y-[-100px]', 'opacity-0');
      toast.classList.add('translate-y-0', 'opacity-100');
      
      setTimeout(() => {
        toast.classList.add('translate-y-[-100px]', 'opacity-0');
        toast.classList.remove('translate-y-0', 'opacity-100');
      }, 3500);
    }

    function switchTab(tab) {
      const tabWebBtn = document.getElementById('tabWebBtn');
      const tabDeviceBtn = document.getElementById('tabDeviceBtn');
      const tabWebContent = document.getElementById('tabWebContent');
      const tabDeviceContent = document.getElementById('tabDeviceContent');

      if (tab === 'web') {
        tabWebBtn.className = "px-4 py-2 rounded-xl text-sm font-semibold transition bg-github-blue/15 text-github-blue border border-github-blue/30";
        tabDeviceBtn.className = "px-4 py-2 rounded-xl text-sm font-semibold transition text-github-muted hover:text-white border border-transparent";
        tabWebContent.classList.remove('hidden');
        tabDeviceContent.classList.add('hidden');
      } else {
        tabDeviceBtn.className = "px-4 py-2 rounded-xl text-sm font-semibold transition bg-github-blue/15 text-github-blue border border-github-blue/30";
        tabWebBtn.className = "px-4 py-2 rounded-xl text-sm font-semibold transition text-github-muted hover:text-white border border-transparent";
        tabDeviceContent.classList.remove('hidden');
        tabWebContent.classList.add('hidden');
      }
    }

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        currentConfig = data;

        // Client ID
        const clientIdText = document.getElementById('clientIdText');
        const clientIdDot = document.getElementById('clientIdDot');
        if (data.client_id) {
          clientIdText.innerText = data.client_id.substring(0, 7) + '...';
          clientIdDot.className = "w-2 h-2 rounded-full bg-emerald-400";
        } else {
          clientIdText.innerText = 'Chưa cấu hình';
          clientIdDot.className = "w-2 h-2 rounded-full bg-red-400";
        }

        // Secret
        const secretText = document.getElementById('secretText');
        const secretDot = document.getElementById('secretDot');
        if (data.has_client_secret) {
          secretText.innerText = 'Đã nhận dạng';
          secretDot.className = "w-2 h-2 rounded-full bg-emerald-400";
        } else {
          secretText.innerText = 'Thiếu bí mật';
          secretDot.className = "w-2 h-2 rounded-full bg-red-400";
        }

        // Callback URL
        const callbackUrl = window.location.origin + '/callback';
        document.getElementById('callbackUrlDisplay').innerText = callbackUrl;

        // Check if there's an error from query param
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('error')) {
          showErrorBanner('Đăng nhập thất bại: ' + urlParams.get('error'), urlParams.get('error_description') || '');
        }

        // Render Authenticated State if available
        if (data.current_token && data.user_profile) {
          renderAuthSection(data);
        } else {
          document.getElementById('authSection').classList.add('hidden');
          document.getElementById('loginSection').classList.remove('hidden');
        }

      } catch (err) {
        console.error("Status fetch failed:", err);
      }
    }

    function renderAuthSection(data) {
      document.getElementById('authSection').classList.remove('hidden');
      document.getElementById('loginSection').classList.add('hidden');

      const profile = data.user_profile;
      document.getElementById('userAvatar').src = profile.avatar_url || '';
      document.getElementById('userName').innerText = profile.name || profile.login;
      document.getElementById('userLogin').innerText = '@' + profile.login;
      document.getElementById('userBio').innerText = profile.bio || 'Không có tiểu sử';
      document.getElementById('userEmail').innerText = '📧 ' + (profile.email || 'Email private/ẩn');
      document.getElementById('userRepos').innerText = '📦 ' + (profile.public_repos ?? 0) + ' repos';

      document.getElementById('tokenInput').value = data.current_token;

      // Scopes
      const scopesContainer = document.getElementById('grantedScopesList');
      scopesContainer.innerHTML = '';
      const scopes = data.token_scopes || [];
      document.getElementById('scopeCount').innerText = scopes.length + ' scopes';
      if (scopes.length === 0) {
        scopesContainer.innerHTML = '<span class="text-xs text-gray-500 italic">Mặc định (public_read)</span>';
      } else {
        scopes.forEach(s => {
          const span = document.createElement('span');
          span.className = "px-2 py-0.5 rounded-md text-[11px] font-mono bg-github-blue/15 text-github-blue border border-github-blue/30";
          span.innerText = s;
          scopesContainer.appendChild(span);
        });
      }

      // Rate limit
      if (data.rate_limit) {
        const remaining = data.rate_limit.remaining ?? 5000;
        const limit = data.rate_limit.limit ?? 5000;
        document.getElementById('rateLimitText').innerText = `${remaining} / ${limit}`;
        const pct = Math.max(0, Math.min(100, Math.round((remaining / limit) * 100)));
        document.getElementById('rateLimitBar').style.width = pct + '%';
        if (data.rate_limit.reset) {
          const resetDate = new Date(data.rate_limit.reset * 1000);
          document.getElementById('rateLimitReset').innerText = 'Reset lúc: ' + resetDate.toLocaleTimeString();
        }
      }
    }

    function showErrorBanner(title, desc) {
      const banner = document.getElementById('errorBanner');
      document.getElementById('errorTitle').innerText = title;
      document.getElementById('errorMessage').innerText = desc;
      banner.classList.remove('hidden');
    }

    function toggleTokenVisibility() {
      const input = document.getElementById('tokenInput');
      const btn = document.getElementById('toggleTokenText');
      if (input.type === 'password') {
        input.type = 'text';
        btn.innerText = 'Ẩn';
      } else {
        input.type = 'password';
        btn.innerText = 'Hiện';
      }
    }

    function copyToken() {
      const token = document.getElementById('tokenInput').value;
      if (!token) return;
      navigator.clipboard.writeText(token).then(() => {
        showToast('Đã copy token vào bộ nhớ tạm!');
      });
    }

    function copyCallbackUrl() {
      const url = document.getElementById('callbackUrlDisplay').innerText;
      navigator.clipboard.writeText(url).then(() => {
        showToast('Đã copy Callback URL!');
      });
    }

    function getSelectedScopes() {
      const checkboxes = document.querySelectorAll('.scope-checkbox:checked');
      const scopes = Array.from(checkboxes).map(cb => cb.value);
      return scopes.join(' ');
    }

    function startWebLogin() {
      if (!currentConfig.client_id) {
        alert('Chưa tìm thấy GITHUB_CLIENT_ID trong .env!');
        return;
      }
      const scopes = getSelectedScopes();
      const redirectUri = window.location.origin + '/callback';
      window.location.href = `/login?scopes=${encodeURIComponent(scopes)}&redirect_uri=${encodeURIComponent(redirectUri)}`;
    }

    function reAuthenticate() {
      document.getElementById('authSection').classList.add('hidden');
      document.getElementById('loginSection').classList.remove('hidden');
    }

    async function saveTokenToEnv() {
      const token = document.getElementById('tokenInput').value;
      if (!token) return;
      try {
        const res = await fetch('/api/save-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token })
        });
        const d = await res.json();
        if (d.success) {
          showToast('Đã lưu GITHUB_TOKEN vào .env thành công!');
        } else {
          showToast('Lỗi: ' + (d.message || 'Không thể lưu'), 'error');
        }
      } catch (err) {
        showToast('Lỗi kết nối máy chủ', 'error');
      }
    }

    async function logout() {
      if (confirm('Bạn có chắc muốn đăng xuất?')) {
        await fetch('/api/logout', { method: 'POST' });
        window.location.href = '/';
      }
    }

    async function testApi(endpoint) {
      const out = document.getElementById('apiTestOutput');
      out.classList.remove('hidden');
      out.innerText = 'Đang gọi ' + endpoint + '...';
      try {
        const token = document.getElementById('tokenInput').value;
        const res = await fetch('/api/test-api', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint, token })
        });
        const data = await res.json();
        out.innerText = JSON.stringify(data, null, 2);
      } catch (err) {
        out.innerText = 'Lỗi gọi API: ' + err.message;
      }
    }

    // Device Flow Handlers
    async function startDeviceLogin() {
      const scopes = getSelectedScopes();
      const btn = document.querySelector('#deviceStartBox button');
      btn.disabled = true;
      btn.innerText = 'Đang tạo Device Code...';

      try {
        const res = await fetch('/api/device/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scopes })
        });
        const data = await res.json();
        if (data.error) {
          alert('Device Code Error: ' + (data.error_description || data.error));
          btn.disabled = false;
          btn.innerText = 'Tạo mã Device Code';
          return;
        }

        document.getElementById('deviceStartBox').classList.add('hidden');
        document.getElementById('deviceActiveBox').classList.remove('hidden');
        document.getElementById('deviceUserCode').innerText = data.user_code;
        document.getElementById('deviceVerifyLink').href = data.verification_uri || 'https://github.com/login/device';

        // Auto copy code
        navigator.clipboard.writeText(data.user_code);
        showToast('Đã copy mã: ' + data.user_code);

        // Start polling
        const interval = Math.max(5, data.interval || 5) * 1000;
        const deviceCode = data.device_code;
        if (devicePollIntervalId) clearInterval(devicePollIntervalId);
        
        devicePollIntervalId = setInterval(async () => {
          try {
            const pollRes = await fetch('/api/device/poll', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ device_code: deviceCode })
            });
            const pollData = await pollRes.json();
            if (pollData.access_token) {
              clearInterval(devicePollIntervalId);
              showToast('Đăng nhập Device Flow thành công!');
              setTimeout(() => { window.location.href = '/?auth=success'; }, 1000);
            } else if (pollData.error && pollData.error !== 'authorization_pending') {
              if (pollData.error !== 'slow_down') {
                clearInterval(devicePollIntervalId);
                document.getElementById('devicePollStatus').innerText = 'Lỗi: ' + pollData.error;
              }
            }
          } catch (e) {
            console.error("Poll error", e);
          }
        }, interval);

      } catch (err) {
        alert('Lỗi: ' + err.message);
        btn.disabled = false;
        btn.innerText = 'Tạo mã Device Code';
      }
    }

    function copyDeviceCode() {
      const code = document.getElementById('deviceUserCode').innerText;
      navigator.clipboard.writeText(code).then(() => {
        showToast('Đã copy mã: ' + code);
      });
    }

    // Initialize on load
    window.addEventListener('DOMContentLoaded', fetchStatus);
  </script>
</body>
</html>
"""

class OAuthRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Clean logging
        print(f"[HTTP] {self.command} {self.path} - {args[1]}")

    def send_json(self, status_code: int, data: Any):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            body = HTML_TEMPLATE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        elif path == "/api/status":
            app_state.reload_config()
            status_data = {
                "client_id": app_state.client_id,
                "has_client_secret": bool(app_state.client_secret),
                "current_token": app_state.current_token,
                "user_profile": app_state.user_profile,
                "rate_limit": app_state.rate_limit,
                "token_scopes": app_state.token_scopes,
                "port": app_state.port,
            }
            self.send_json(200, status_data)
            return

        elif path == "/login":
            app_state.reload_config()
            if not app_state.client_id:
                self.send_response(302)
                self.send_header("Location", "/?error=missing_client_id&error_description=Chua+co+GITHUB_CLIENT_ID+trong+.env")
                self.end_headers()
                return

            scopes = query.get("scopes", ["repo read:user user:email"])[0]
            redirect_uri = query.get("redirect_uri", [f"http://localhost:{app_state.port}/callback"])[0]
            
            # CSRF State
            state_token = secrets.token_urlsafe(16)
            app_state.active_states.add(state_token)

            params = {
                "client_id": app_state.client_id,
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "state": state_token,
                "allow_signup": "true"
            }
            github_auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
            self.send_response(302)
            self.send_header("Location", github_auth_url)
            self.end_headers()
            return

        elif path == "/callback":
            code = query.get("code", [None])[0]
            state = query.get("state", [None])[0]
            err = query.get("error", [None])[0]
            err_desc = query.get("error_description", [None])[0]

            if err:
                self.send_response(302)
                self.send_header("Location", f"/?error={urllib.parse.quote(err)}&error_description={urllib.parse.quote(err_desc or '')}")
                self.end_headers()
                return

            if not code:
                self.send_response(302)
                self.send_header("Location", "/?error=missing_code&error_description=Khong+nhan+duoc+code+tu+GitHub")
                self.end_headers()
                return

            # Exchange code for token with GitHub
            token_url = "https://github.com/login/oauth/access_token"
            payload = {
                "client_id": app_state.client_id,
                "client_secret": app_state.client_secret,
                "code": code,
            }
            status_code, headers, data = make_http_request(token_url, method="POST", body=payload)
            
            if status_code == 200 and isinstance(data, dict) and "access_token" in data:
                token = data["access_token"]
                app_state.current_token = token
                # Save immediately to .env
                save_token_to_env(token)
                # Fetch profile and scopes
                app_state.fetch_user_profile(token)
                
                print(f"[+] Successfully authenticated! Logged in as: {app_state.user_profile.get('login') if app_state.user_profile else 'Unknown'}")
                print(f"[+] Token saved to .env as GITHUB_TOKEN")

                self.send_response(302)
                self.send_header("Location", "/?auth=success")
                self.end_headers()
                return
            else:
                error_msg = data.get("error_description") or data.get("error") or "Không thể lấy Access Token từ GitHub"
                print(f"[-] Token exchange failed: {data}")
                self.send_response(302)
                self.send_header("Location", f"/?error=token_exchange_failed&error_description={urllib.parse.quote(str(error_msg))}")
                self.end_headers()
                return

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        post_data = {}
        if content_len > 0:
            raw = self.rfile.read(content_len).decode("utf-8")
            try:
                post_data = json.loads(raw)
            except Exception:
                post_data = {}

        if path == "/api/save-token":
            token = post_data.get("token", "").strip()
            if not token:
                self.send_json(400, {"success": False, "message": "Token cannot be empty"})
                return
            success = save_token_to_env(token)
            if success:
                app_state.current_token = token
                app_state.fetch_user_profile(token)
                self.send_json(200, {"success": True, "message": "Token saved to .env"})
            else:
                self.send_json(500, {"success": False, "message": "Failed to write to .env"})
            return

        elif path == "/api/logout":
            app_state.current_token = ""
            app_state.user_profile = None
            app_state.rate_limit = None
            app_state.token_scopes = []
            self.send_json(200, {"success": True})
            return

        elif path == "/api/test-api":
            endpoint = post_data.get("endpoint", "/user")
            token = post_data.get("token", app_state.current_token)
            if not token:
                self.send_json(400, {"error": "No token available"})
                return
            url = f"https://api.github.com{endpoint}" if endpoint.startswith("/") else f"https://api.github.com/{endpoint}"
            status_code, headers, data = make_http_request(
                url,
                headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
            )
            self.send_json(status_code, data)
            return

        elif path == "/api/device/start":
            app_state.reload_config()
            scopes = post_data.get("scopes", "repo read:user user:email")
            code_url = "https://github.com/login/device/code"
            payload = {
                "client_id": app_state.client_id,
                "scope": scopes
            }
            status_code, _, data = make_http_request(code_url, method="POST", body=payload)
            self.send_json(status_code, data)
            return

        elif path == "/api/device/poll":
            app_state.reload_config()
            device_code = post_data.get("device_code", "")
            poll_url = "https://github.com/login/oauth/access_token"
            payload = {
                "client_id": app_state.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }
            status_code, _, data = make_http_request(poll_url, method="POST", body=payload)
            if status_code == 200 and isinstance(data, dict) and "access_token" in data:
                token = data["access_token"]
                app_state.current_token = token
                save_token_to_env(token)
                app_state.fetch_user_profile(token)
            self.send_json(status_code, data)
            return

        else:
            self.send_json(404, {"error": "Not found"})

def run_server(port: int = 8000, open_browser: bool = True):
    app_state.port = port
    server_address = ("127.0.0.1", port)
    
    try:
        httpd = ThreadingHTTPServer(server_address, OAuthRequestHandler)
    except OSError as e:
        if e.errno == 48:  # Address already in use
            print(f"[!] Cổng {port} đang bị chiếm dụng. Đang thử cổng {port + 1}...")
            run_server(port + 1, open_browser)
            return
        raise

    url = f"http://localhost:{port}"
    print("=" * 68)
    print("🚀 GITHUB OAUTH LOGIN PORTAL ĐÃ KHỞI CHẠY THÀNH CÔNG!")
    print("=" * 68)
    print(f"📍 Web UI URL:          {url}")
    print(f"🔑 Client ID:           {app_state.client_id[:8]}... (từ .env)")
    print(f"🔒 Client Secret:       {'Có sẵn trong .env' if app_state.client_secret else 'Chưa tìm thấy!'}")
    print(f"🎯 Callback URL:        {url}/callback")
    print("=" * 68)
    print("💡 Mở trình duyệt và nhấn 'Đăng nhập với GitHub' để nhận Access Token.")
    print("💡 Token sẽ tự động được ghi vào file .env dưới biến GITHUB_TOKEN.")
    print("💡 Nhấn Ctrl + C để dừng server.")
    print("=" * 68)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Đang tắt server...")
        httpd.server_close()
        print("[+] Đã tắt server thành công.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GitHub OAuth Login UI Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the local server on (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open the default web browser")
    args = parser.parse_args()

    run_server(port=args.port, open_browser=not args.no_browser)
