import os
import tarfile
import json
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import paramiko

class GUIHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = ":".join(f"{x:02x}" for x in key.get_fingerprint())
        msg = f"The authenticity of host '{hostname}' can't be established.\n" \
              f"{key.get_name()} key fingerprint is {fingerprint}.\n" \
              f"Are you sure you want to continue connecting?"
        if messagebox.askyesno("Unknown Host", msg):
            client.get_host_keys().add(hostname, key.get_name(), key)
            if client._host_keys_filename is not None:
                client.save_host_keys(client._host_keys_filename)
            else:
                known_hosts_path = os.path.expanduser('~/.ssh/known_hosts')
                os.makedirs(os.path.dirname(known_hosts_path), exist_ok=True)
                client.save_host_keys(known_hosts_path)
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

        ttk.Label(info_frame, text=f"Server: {self.config['hostname']}").pack(anchor="w")
        ttk.Label(info_frame, text=f"Remote Path: {self.config['remote_path']}").pack(anchor="w")
        
        sources = ", ".join([os.path.basename(os.path.expanduser(d)) for d in self.config['source_directories']])
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
        creds_frame.columnconfigure(1, weight=1)

        # Progress Section
        self.progress_label = ttk.Label(self.root, text="Ready to Snapshot", font=("Arial", 10, "bold"))
        self.progress_label.pack(pady=10)

        self.stats_label = ttk.Label(self.root, text="0 MB transferred @ 0 MB/s")
        self.stats_label.pack()

        # Action Button
        self.btn_start = ttk.Button(self.root, text="Start Snapshot", command=self.start_backup_thread)
        self.btn_start.pack(pady=20)

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
            
            ssh = paramiko.SSHClient()
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(GUIHostKeyPolicy())
            ssh.connect(self.config['hostname'], self.config.get('port', 22), username, password)
            
            sftp = ssh.open_sftp()
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{socket.gethostname()}_{timestamp}.tar.gz"
            remote_full_path = os.path.join(self.config['remote_path'], filename).replace('\\', '/')

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
                    def write(self, data):
                        self.proxy.write(data)
                        self.total_written += len(data)
                        self.callback(self.total_written, 0)
                    def close(self): self.proxy.close()
                    def flush(self): self.proxy.flush()

                monitored_file = ProgressFile(remote_file, self.update_stats)

                excludes = [os.path.abspath(os.path.expanduser(d)) for d in self.config.get('excluded_directories', [])]

                with tarfile.open(fileobj=monitored_file, mode='w:gz') as tar:
                    for directory in self.config['source_directories']:
                        expanded_dir = os.path.abspath(os.path.expanduser(directory))
                        if os.path.exists(expanded_dir):
                            arc_base = os.path.basename(expanded_dir)
                            
                            def filter_tar(tarinfo, src_dir=expanded_dir, arc_name=arc_base):
                                rel_path = os.path.relpath(tarinfo.name, arc_name)
                                orig_path = os.path.abspath(os.path.join(src_dir, rel_path))
                                for ex in excludes:
                                    if orig_path == ex or orig_path.startswith(ex + os.sep):
                                        return None
                                return tarinfo
                                
                            tar.add(expanded_dir, arcname=arc_base, filter=filter_tar)

            self.last_update_time = 0
            self.update_stats(monitored_file.total_written, 0)
            self.progress_label.config(text="Backup Complete!", foreground="green")
            messagebox.showinfo("Success", f"Archive streamed to {remote_full_path}")
            
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
