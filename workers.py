#!/usr/bin/env python3
"""Unified worker manager for Foundry local workers.

Usage:
  ./workers.py status              # Show status of all workers
  ./workers.py start [worker...]   # Start all workers (or specific ones)
  ./workers.py stop [worker...]    # Stop all workers (or specific ones)
  ./workers.py restart [worker...] # Restart all workers (or specific ones)
  ./workers.py logs [worker]       # Show logs (tail -f) for a worker
  ./workers.py list                # List available workers
  ./workers.py check               # Check configuration and dependencies

Available workers: cursor, project_sync, foundry_eval
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config_loader import load_unified_config


class WorkerManager:
    def __init__(self):
        self.config = load_unified_config()
        self.root = ROOT
        self.var_dir = self.root / "var"
        self.var_dir.mkdir(exist_ok=True)
        self.pid_dir = self.var_dir / "pids"
        self.pid_dir.mkdir(exist_ok=True)
        self.log_dir = self.var_dir / "logs"
        self.log_dir.mkdir(exist_ok=True)

    def get_worker_config(self, worker: str) -> dict[str, Any] | None:
        """Get configuration for a specific worker."""
        return self.config.get("workers", {}).get(worker)

    def list_workers(self) -> list[str]:
        """List all configured workers."""
        return list(self.config.get("workers", {}).keys())

    def get_worker_cwd(self, worker: str) -> Path:
        """Get the working directory for a worker."""
        worker_cfg = self.get_worker_config(worker)
        if not worker_cfg:
            return self.root
        
        working_dir = worker_cfg.get("working_dir", "")
        if working_dir:
            # Replace ${var} references
            working_dir = working_dir.replace("${foundry_eval_path}", self.config["foundry_eval_path"])
            return Path(working_dir).expanduser().resolve()
        return self.root

    def get_worker_venv(self, worker: str) -> Path:
        """Get the venv path for a worker."""
        cwd = self.get_worker_cwd(worker)
        return cwd / ".venv"

    def get_pid_file(self, worker: str) -> Path:
        """Get the PID file path for a worker."""
        return self.pid_dir / f"{worker}.pid"

    def get_log_file(self, worker: str) -> Path:
        """Get the log file path for a worker."""
        return self.log_dir / f"{worker}.log"

    def is_running(self, worker: str) -> bool:
        """Check if a worker is currently running."""
        pid_file = self.get_pid_file(worker)
        if not pid_file.exists():
            return False
        
        try:
            pid = int(pid_file.read_text().strip())
            # Check if process is alive
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # PID file exists but process is dead
            pid_file.unlink(missing_ok=True)
            return False

    def get_pid(self, worker: str) -> int | None:
        """Get the PID of a running worker."""
        if not self.is_running(worker):
            return None
        pid_file = self.get_pid_file(worker)
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, FileNotFoundError):
            return None

    def check_env(self, worker: str) -> tuple[bool, list[str]]:
        """Check if required environment variables are set."""
        worker_cfg = self.get_worker_config(worker)
        if not worker_cfg:
            return False, ["Worker not found in configuration"]
        
        missing = []
        for var in worker_cfg.get("env_required", []):
            if not os.environ.get(var):
                missing.append(var)
        
        return len(missing) == 0, missing

    def check_worker(self, worker: str) -> tuple[bool, list[str]]:
        """Check if a worker is properly configured."""
        issues = []
        
        worker_cfg = self.get_worker_config(worker)
        if not worker_cfg:
            return False, ["Worker not found in configuration"]
        
        # Check if enabled
        if not worker_cfg.get("enabled", True):
            issues.append("Worker is disabled in configuration")
        
        # Check working directory
        cwd = self.get_worker_cwd(worker)
        if not cwd.exists():
            issues.append(f"Working directory does not exist: {cwd}")
        
        # Check venv
        venv = self.get_worker_venv(worker)
        if not venv.exists():
            issues.append(f"Virtual environment not found: {venv}")
        
        # Check Python executable
        python_exe = venv / "bin" / "python"
        if not python_exe.exists():
            issues.append(f"Python executable not found: {python_exe}")
        
        # Check .env file
        env_file = cwd / ".env"
        if not env_file.exists() and worker != "foundry_eval":
            issues.append(f".env file not found: {env_file}")
        
        return len(issues) == 0, issues

    def start_worker(self, worker: str) -> bool:
        """Start a worker in the background."""
        if self.is_running(worker):
            print(f"✓ {worker} is already running (PID {self.get_pid(worker)})")
            return True
        
        worker_cfg = self.get_worker_config(worker)
        if not worker_cfg:
            print(f"✗ {worker}: not found in configuration")
            return False
        
        if not worker_cfg.get("enabled", True):
            print(f"✗ {worker}: disabled in configuration")
            return False
        
        # Check configuration
        ok, issues = self.check_worker(worker)
        if not ok:
            print(f"✗ {worker}: configuration issues:")
            for issue in issues:
                print(f"  - {issue}")
            return False
        
        cwd = self.get_worker_cwd(worker)
        venv = self.get_worker_venv(worker)
        python_exe = venv / "bin" / "python"
        command = worker_cfg.get("command", "")
        
        if not command:
            print(f"✗ {worker}: no command specified")
            return False
        
        log_file = self.get_log_file(worker)
        pid_file = self.get_pid_file(worker)
        
        # Build command
        cmd_parts = command.split()
        full_cmd = [str(python_exe)] + cmd_parts[1:]  # Replace 'python' with venv python
        
        print(f"Starting {worker}...")
        print(f"  CWD: {cwd}")
        print(f"  CMD: {' '.join(full_cmd)}")
        print(f"  LOG: {log_file}")
        
        try:
            with log_file.open("a") as log:
                log.write(f"\n{'='*60}\n")
                log.write(f"Starting at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                log.write(f"Command: {' '.join(full_cmd)}\n")
                log.write(f"{'='*60}\n\n")
                log.flush()
                
                proc = subprocess.Popen(
                    full_cmd,
                    cwd=str(cwd),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                
                # Write PID file
                pid_file.write_text(str(proc.pid))
                
                # Give it a moment to start
                time.sleep(1)
                
                # Check if it's still running
                if self.is_running(worker):
                    print(f"✓ {worker} started (PID {proc.pid})")
                    return True
                else:
                    print(f"✗ {worker} failed to start (check {log_file})")
                    return False
        except Exception as e:
            print(f"✗ {worker} failed to start: {e}")
            return False

    def stop_worker(self, worker: str) -> bool:
        """Stop a running worker."""
        if not self.is_running(worker):
            print(f"✓ {worker} is not running")
            return True
        
        pid = self.get_pid(worker)
        if pid is None:
            print(f"✗ {worker}: could not determine PID")
            return False
        
        print(f"Stopping {worker} (PID {pid})...")
        try:
            # Try graceful shutdown first
            os.kill(pid, 15)  # SIGTERM
            
            # Wait up to 10 seconds for it to stop
            for _ in range(20):
                if not self.is_running(worker):
                    print(f"✓ {worker} stopped")
                    return True
                time.sleep(0.5)
            
            # Force kill if still running
            print(f"  Force stopping {worker}...")
            os.kill(pid, 9)  # SIGKILL
            time.sleep(0.5)
            
            if not self.is_running(worker):
                print(f"✓ {worker} stopped (forced)")
                return True
            else:
                print(f"✗ {worker}: failed to stop")
                return False
        except Exception as e:
            print(f"✗ {worker} failed to stop: {e}")
            return False

    def show_status(self, workers: list[str] | None = None):
        """Show status of workers."""
        if workers is None:
            workers = self.list_workers()
        
        print("Foundry Worker Status")
        print("=" * 70)
        
        for worker in workers:
            worker_cfg = self.get_worker_config(worker)
            if not worker_cfg:
                print(f"✗ {worker}: not found in configuration")
                continue
            
            desc = worker_cfg.get("description", "")
            enabled = worker_cfg.get("enabled", True)
            
            status = "●" if self.is_running(worker) else "○"
            status_text = "RUNNING" if self.is_running(worker) else "STOPPED"
            
            pid = self.get_pid(worker) or "-"
            
            print(f"\n{status} {worker}: {status_text}")
            print(f"  Description: {desc}")
            print(f"  Enabled: {enabled}")
            if self.is_running(worker):
                print(f"  PID: {pid}")
                log_file = self.get_log_file(worker)
                print(f"  Log: {log_file}")
            
            # Check configuration
            ok, issues = self.check_worker(worker)
            if not ok and issues:
                print(f"  Issues:")
                for issue in issues:
                    print(f"    - {issue}")
        
        print()

    def tail_logs(self, worker: str):
        """Tail logs for a worker."""
        log_file = self.get_log_file(worker)
        
        if not log_file.exists():
            print(f"No log file found: {log_file}")
            return
        
        print(f"Tailing {log_file} (Ctrl+C to stop)...")
        print("-" * 70)
        
        try:
            subprocess.run(["tail", "-f", str(log_file)])
        except KeyboardInterrupt:
            print("\nStopped tailing")

    def check_all(self):
        """Check configuration and dependencies for all workers."""
        print("Configuration Check")
        print("=" * 70)
        print(f"\nRepos root: {self.config['repos_root']}")
        print(f"Foundry eval path: {self.config['foundry_eval_path']}")
        print(f"\nLog directory: {self.log_dir}")
        print(f"PID directory: {self.pid_dir}")
        
        all_ok = True
        
        for worker in self.list_workers():
            print(f"\n{worker}:")
            worker_cfg = self.get_worker_config(worker)
            print(f"  Enabled: {worker_cfg.get('enabled', True)}")
            print(f"  Command: {worker_cfg.get('command', '')}")
            
            cwd = self.get_worker_cwd(worker)
            print(f"  Working directory: {cwd}")
            print(f"    Exists: {'✓' if cwd.exists() else '✗'}")
            
            venv = self.get_worker_venv(worker)
            print(f"  Virtual environment: {venv}")
            print(f"    Exists: {'✓' if venv.exists() else '✗'}")
            
            env_file = cwd / ".env"
            print(f"  Environment file: {env_file}")
            print(f"    Exists: {'✓' if env_file.exists() else '✗' if worker != 'foundry_eval' else 'N/A'}")
            
            ok, issues = self.check_worker(worker)
            if not ok:
                print(f"  Status: ✗ Issues found")
                all_ok = False
            else:
                print(f"  Status: ✓ OK")
        
        print("\n" + "=" * 70)
        if all_ok:
            print("✓ All checks passed")
        else:
            print("✗ Some checks failed - review issues above")
        
        return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="Manage Foundry local workers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=["start", "stop", "restart", "status", "logs", "list", "check"],
        help="Command to execute",
    )
    parser.add_argument(
        "workers",
        nargs="*",
        help="Specific workers to manage (default: all)",
    )
    
    args = parser.parse_args()
    
    manager = WorkerManager()
    
    if args.command == "list":
        print("Available workers:")
        for worker in manager.list_workers():
            cfg = manager.get_worker_config(worker)
            desc = cfg.get("description", "") if cfg else ""
            enabled = cfg.get("enabled", True) if cfg else False
            status = "enabled" if enabled else "disabled"
            print(f"  - {worker}: {desc} ({status})")
        return 0
    
    if args.command == "check":
        return 0 if manager.check_all() else 1
    
    if args.command == "logs":
        if not args.workers:
            print("Error: specify a worker to tail logs")
            return 1
        manager.tail_logs(args.workers[0])
        return 0
    
    workers = args.workers if args.workers else manager.list_workers()
    
    if args.command == "status":
        manager.show_status(workers)
        return 0
    
    if args.command == "start":
        results = [manager.start_worker(w) for w in workers]
        return 0 if all(results) else 1
    
    if args.command == "stop":
        results = [manager.stop_worker(w) for w in workers]
        return 0 if all(results) else 1
    
    if args.command == "restart":
        stop_results = [manager.stop_worker(w) for w in workers]
        time.sleep(1)
        start_results = [manager.start_worker(w) for w in workers]
        return 0 if all(stop_results) and all(start_results) else 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
