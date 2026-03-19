import os
import tarfile
import json
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import paramiko
import hashlib
import csv
from datetime import datetime, timezone
import subprocess
import platform

def ping_host(hostname):
    """Returns the ping response time in seconds, or float('inf') if failed."""
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    command = ['ping', param, '1', hostname]
    try:
        start = time.time()
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        if result.returncode == 0:
            return time.time() - start
    except Exception:
        pass
    return float('inf')

class GUIHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = ":".join(f"{x:02x}" for x in key.get_fingerprint())
        msg = f"The authenticity of host '{hostname}' can't be established.\n" \
              f"{key.get_name()} key fingerprint is {fingerprint}.\n" \
              f"Are you sure you want to continue connecting?"
        if messagebox.askyesno("Unknown Host", msg):
            client.get_host_keys().add(hostname, key.get_name(), key)
            try:
                if client._host_keys_filename is not None:
                    client.save_host_keys(client._host_keys_filename)
                else:
                    known_hosts_path = os.path.expanduser('~/.ssh/known_hosts')
                    os.makedirs(os.path.dirname(known_hosts_path), exist_ok=True)
                    client.save_host_keys(known_hosts_path)
            except Exception:
                # Ignore errors when saving host keys
                pass
            return
        raise paramiko.SSHException(f"Server {hostname} not found in known_hosts")

class BackupGUI:
    def __init__(self, root, config_path='config.json'):
        self.root = root
        self.root.title("SFTP Backup Tool")
        self.root.geometry("500x400")
        self.config_path = config_path
        
        # Load Config
        with open(self.config_path, 'r') as f:
            self.config = json.load(f)

        self.setup_ui()

    def setup_ui(self):
        # Info Section
        info_frame = ttk.LabelFrame(self.root, text="Configuration", padding=10)
        info_frame.pack(fill="x", padx=10, pady=5)

        server_frame = ttk.Frame(info_frame)
        server_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(server_frame, text="Server:").pack(side="left")

        servers = self.config.get('servers', [])
        # Fallback for old config format
        if not servers and 'hostname' in self.config:
            servers = [self.config]
            self.config['servers'] = servers

        self.server_names = ["auto"] + [s.get('name', s.get('hostname', 'Unknown')) for s in servers]
        self.server_combo = ttk.Combobox(server_frame, values=self.server_names, state="readonly")
        self.server_combo.set("auto")
        self.server_combo.pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.server_info_label = ttk.Label(info_frame, text="")
        self.server_info_label.pack(anchor="w")
        self.server_combo.bind("<<ComboboxSelected>>", self.on_server_selected)
        
        sources = ", ".join([os.path.basename(os.path.expanduser(d)) for d in self.config.get('source_directories', [])])
        ttk.Label(info_frame, text=f"Sources: {sources}", wraplength=450).pack(anchor="w")

        excludes = self.config.get('excluded_directories', [])
        if excludes:
            excludes_frame = ttk.Frame(info_frame)
            excludes_frame.pack(fill="x", pady=(5, 0))
            ttk.Label(excludes_frame, text="Excluded:").pack(side="left")
            self.excludes_combo = ttk.Combobox(excludes_frame, values=excludes, state="readonly")
            self.excludes_combo.set(f"{len(excludes)} directories excluded")
            self.excludes_combo.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Credentials Section
        creds_frame = ttk.Frame(self.root, padding=10)
        creds_frame.pack(fill="x")

        ttk.Label(creds_frame, text="Username:").grid(row=0, column=0, sticky="w")
        self.user_entry = ttk.Entry(creds_frame)
        self.user_entry.insert(0, self.config.get('username', ''))
        self.user_entry.grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(creds_frame, text="Password:").grid(row=1, column=0, sticky="w")
        self.pass_entry = ttk.Entry(creds_frame, show="*")
        self.pass_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.pass_entry.bind('<Return>', lambda e: self.start_backup_thread())
        creds_frame.columnconfigure(1, weight=1)

        # Progress Section
        self.progress_label = ttk.Label(self.root, text="Ready to Snapshot", font=("Arial", 10, "bold"))
        self.progress_label.pack(pady=10)

        self.stats_label = ttk.Label(self.root, text="0 MB transferred @ 0 MB/s")
        self.stats_label.pack()

        # Action Button
        self.btn_start = ttk.Button(self.root, text="Start Snapshot", command=self.start_backup_thread)
        self.btn_start.pack(pady=20)
        
        # Initialize UI state based on selection
        self.on_server_selected()

    def on_server_selected(self, event=None):
        selection = self.server_combo.get()
        if selection == "auto":
            self.server_info_label.config(text="Host: Multiple\nRemote Path: Default")
            first_server = self.config.get('servers', [{}])[0]
            if hasattr(self, 'user_entry'):
                self.user_entry.delete(0, tk.END)
                self.user_entry.insert(0, first_server.get('username', ''))
        else:
            server = next((s for s in self.config.get('servers', []) if s.get('name', s.get('hostname')) == selection), {})
            self.server_info_label.config(text=f"Host: {server.get('hostname')}\nRemote Path: {server.get('remote_path')}")
            if hasattr(self, 'user_entry'):
                self.user_entry.delete(0, tk.END)
                self.user_entry.insert(0, server.get('username', ''))

    def update_stats(self, transferred, total):
        """Callback for Paramiko to update the GUI progress"""
        current_time = time.time()
        
        if not hasattr(self, 'last_update_time'):
            self.last_update_time = 0
            
        if current_time - self.last_update_time >= 1.0:
            elapsed = current_time - self.start_time
            
            mb_sent = transferred / (1024 * 1024)
            # Calculate rate (MB/s)
            rate = mb_sent / elapsed if elapsed > 0 else 0
            
            self.stats_label.config(
                text=f"Sent: {mb_sent:.2f} MB | Speed: {rate:.2f} MB/s"
            )
            self.root.update_idletasks()
            self.last_update_time = current_time

    def start_backup_thread(self):
        self.btn_start.config(state="disabled")
        self.progress_label.config(text="Backup in progress...", foreground="blue")
        threading.Thread(target=self.run_backup, daemon=True).start()

    def run_backup(self):
        try:
            username = self.user_entry.get()
            password = self.pass_entry.get()
            
            selection = self.server_combo.get()
            servers = self.config.get('servers', [])
            
            if selection == "auto":
                self.progress_label.config(text="Pinging servers to find fastest...", foreground="blue")
                
                best_server = None
                best_time = float('inf')
                for s in servers:
                    hostname = s.get('hostname')
                    if not hostname: continue
                    t = ping_host(hostname)
                    if t < best_time:
                        best_time = t
                        best_server = s
                
                if not best_server or best_time == float('inf'):
                    raise Exception("All servers are unreachable or ping failed.")
                    
                target_server = best_server
                self.progress_label.config(text=f"Selected {target_server.get('name', target_server.get('hostname'))} (auto)", foreground="blue")
            else:
                target_server = next((s for s in servers if s.get('name', s.get('hostname')) == selection), None)
                if not target_server:
                    raise Exception("Selected server not found in config.")

            hostname = target_server.get('hostname')
            port = target_server.get('port', 22)
            remote_path = target_server.get('remote_path')
            
            ssh = paramiko.SSHClient()
            try:
                ssh.load_system_host_keys()
            except Exception:
                # Ignore invalid entries in known_hosts instead of crashing
                pass
            ssh.set_missing_host_key_policy(GUIHostKeyPolicy())
            ssh.connect(hostname, port, username, password)
            
            sftp = ssh.open_sftp()
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{socket.gethostname()}_{timestamp}.tar.gz"
            remote_full_path = os.path.join(remote_path, filename).replace('\\', '/')

            self.start_time = time.time()
            
            # Streaming upload with progress monitoring
            with sftp.file(remote_full_path, 'wb') as remote_file:
                remote_file.set_pipelined(True)  # Enable pipelining for faster SFTP writes
                
                # We wrap the file object to intercept writes for progress
                class ProgressFile:
                    def __init__(self, proxy, callback):
                        self.proxy = proxy
                        self.callback = callback
                        self.total_written = 0
                        self.hasher = hashlib.sha256()
                    def write(self, data):
                        self.proxy.write(data)
                        self.total_written += len(data)
                        self.hasher.update(data)
                        self.callback(self.total_written, 0)
                    def close(self): self.proxy.close()
                    def flush(self): self.proxy.flush()

                monitored_file = ProgressFile(remote_file, self.update_stats)
                manifest = []

                excludes = [os.path.abspath(os.path.expanduser(d)) for d in self.config.get('excluded_directories', [])]

                with tarfile.open(fileobj=monitored_file, mode='w:gz') as tar:
                    for directory in self.config['source_directories']:
                        expanded_dir = os.path.abspath(os.path.expanduser(directory))
                        if os.path.exists(expanded_dir):
                            arc_base = os.path.basename(expanded_dir)
                            for root, dirs, files in os.walk(expanded_dir):
                                dirs[:] = [d for d in dirs if not any(
                                    os.path.abspath(os.path.join(root, d)) == ex or os.path.abspath(os.path.join(root, d)).startswith(ex + os.sep)
                                    for ex in excludes
                                )]
                                rel_root = os.path.relpath(root, expanded_dir)
                                arc_root = os.path.join(arc_base, rel_root) if rel_root != '.' else arc_base
                                arc_root = arc_root.replace('\\', '/')
                                try:
                                    ti = tar.gettarinfo(root, arcname=arc_root)
                                    tar.addfile(ti)
                                except Exception:
                                    pass
                                
                                for file in files:
                                    filepath = os.path.join(root, file)
                                    arc_filepath = os.path.join(arc_root, file).replace('\\', '/')
                                    
                                    if os.path.islink(filepath) or not os.path.isfile(filepath):
                                        try:
                                            ti = tar.gettarinfo(filepath, arcname=arc_filepath)
                                            tar.addfile(ti)
                                        except Exception:
                                            pass
                                        continue
                                        
                                    dt = ''
                                    try:
                                        mtime = os.path.getmtime(filepath)
                                        dt = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                                        ti = tar.gettarinfo(filepath, arcname=arc_filepath)
                                        
                                        class HashingFile:
                                            def __init__(self, fp):
                                                self.f = open(fp, 'rb')
                                                self.hasher = hashlib.sha256()
                                            def read(self, size=-1):
                                                data = self.f.read(size)
                                                if data: self.hasher.update(data)
                                                return data
                                            def close(self): self.f.close()
                                            
                                        hf = HashingFile(filepath)
                                        tar.addfile(ti, fileobj=hf)
                                        manifest.append(['archived', dt, filepath, hf.hasher.hexdigest()])
                                        hf.close()
                                    except Exception:
                                        manifest.append(['failure', dt, filepath, ''])

            # Write manifest CSV
            manifest_basename = f"manifest_{socket.gethostname()}_{timestamp}.csv"
            config_dir = os.path.dirname(os.path.abspath(self.config_path))
            manifest_local_path = os.path.join(config_dir, manifest_basename)
            
            with open(manifest_local_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(['status', 'last_modification_date', 'path', 'sha256_hash'])
                writer.writerow(['', '', filename, monitored_file.hasher.hexdigest()])
                for row in manifest:
                    writer.writerow(row)

            # Upload manifest
            manifest_remote_path = os.path.join(remote_path, manifest_basename).replace('\\', '/')
            sftp.put(manifest_local_path, manifest_remote_path)

            self.last_update_time = 0
            self.update_stats(monitored_file.total_written, 0)
            self.progress_label.config(text="Backup Complete!", foreground="green")
            messagebox.showinfo("Success", f"Archive streamed to {remote_full_path}\nManifest saved and uploaded.")
            
        except Exception as e:
            self.progress_label.config(text="Backup Failed", foreground="red")
            messagebox.showerror("Error", str(e))
        finally:
            self.btn_start.config(state="normal")
            if 'ssh' in locals(): ssh.close()

if __name__ == "__main__":
    root = tk.Tk()
    app = BackupGUI(root)
    root.mainloop()
