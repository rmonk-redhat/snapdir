import os
import tarfile
import json
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import paramiko

class BackupGUI:
    def __init__(self, root, config_path='config.json'):
        self.root = root
        self.root.title("Cloud Snap Backup")
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
        
        sources = ", ".join([os.path.basename(d) for d in self.config['source_directories']])
        ttk.Label(info_frame, text=f"Sources: {sources}", wraplength=450).pack(anchor="w")

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
        elapsed = current_time - self.start_time
        
        mb_sent = transferred / (1024 * 1024)
        # Calculate rate (MB/s)
        rate = mb_sent / elapsed if elapsed > 0 else 0
        
        self.stats_label.config(
            text=f"Sent: {mb_sent:.2f} MB | Speed: {rate:.2f} MB/s"
        )
        self.root.update_idletasks()

    def start_backup_thread(self):
        self.btn_start.config(state="disabled")
        self.progress_label.config(text="Backup in progress...", foreground="blue")
        threading.Thread(target=self.run_backup, daemon=True).start()

    def run_backup(self):
        try:
            username = self.user_entry.get()
            password = self.pass_entry.get()
            
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(self.config['hostname'], self.config.get('port', 22), username, password)
            
            sftp = ssh.open_sftp()
            
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{socket.gethostname()}_{timestamp}.tar.gz"
            remote_full_path = os.path.join(self.config['remote_path'], filename).replace('\\', '/')

            self.start_time = time.time()
            
            # Streaming upload with progress monitoring
            with sftp.file(remote_full_path, 'wb') as remote_file:
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

                with tarfile.open(fileobj=monitored_file, mode='w:gz') as tar:
                    for directory in self.config['source_directories']:
                        if os.path.exists(directory):
                            tar.add(directory, arcname=os.path.basename(directory))

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
