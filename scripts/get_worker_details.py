#!/usr/bin/env python3
import os
import sys
import paramiko
from dotenv import load_dotenv

def get_env_credentials():
    # Dynamic resolution of the .env file path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, "..", ".env")
    if not os.path.exists(dotenv_path):
        dotenv_path = os.path.join(script_dir, ".env")
    if not os.path.exists(dotenv_path):
        dotenv_path = r"c:\Users\hjamet\Documents\code\cluster-ci\.env"
        
    if not os.path.exists(dotenv_path):
        print(f"Error: .env file not found. Checked parent directory, current directory and default path.")
        sys.exit(1)
    
    load_dotenv(dotenv_path)
    
    workers = []
    worker_count = int(os.getenv("WORKER_COUNT", "0"))
    
    for i in range(1, worker_count + 1):
        ip = os.getenv(f"WORKER_{i}_IP")
        user = os.getenv(f"WORKER_{i}_USER")
        password = os.getenv(f"WORKER_{i}_PASS")
        if ip and user and password:
            workers.append({
                "id": i,
                "ip": ip,
                "user": user,
                "password": password
            })
            
    return workers

def get_node_specs(node):
    print(f"Connecting to Worker {node['id']} ({node['ip']})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(
            hostname=node["ip"],
            username=node["user"],
            password=node["password"],
            timeout=10
        )
        print(f"Connected successfully! Retrieving specifications...")
        
        specs = {}
        
        # OS Pretty Name
        stdin, stdout, stderr = ssh.exec_command("cat /etc/os-release | grep PRETTY_NAME")
        os_out = stdout.read().decode("utf-8").strip()
        specs["os"] = os_out.split("=")[-1].strip('"') if os_out else "Unknown Linux"
        
        # Kernel & Arch
        stdin, stdout, stderr = ssh.exec_command("uname -mr")
        specs["kernel"] = stdout.read().decode("utf-8").strip()
        
        # CPU Info
        stdin, stdout, stderr = ssh.exec_command("lscpu | grep 'Model name'")
        cpu_out = stdout.read().decode("utf-8").strip()
        if not cpu_out:
            stdin, stdout, stderr = ssh.exec_command("uname -m")
            cpu_out = stdout.read().decode("utf-8").strip()
        specs["cpu"] = cpu_out.replace("Model name:", "").strip()
        
        # RAM
        stdin, stdout, stderr = ssh.exec_command("free -h | grep Mem")
        ram_out = stdout.read().decode("utf-8").strip()
        if ram_out:
            parts = ram_out.split()
            specs["ram_total"] = parts[1]
            specs["ram_available"] = parts[6]
        else:
            specs["ram_total"] = "Unknown"
            specs["ram_available"] = "Unknown"
            
        # Storage
        stdin, stdout, stderr = ssh.exec_command("df -h / | tail -n 1")
        storage_out = stdout.read().decode("utf-8").strip()
        if storage_out:
            parts = storage_out.split()
            specs["storage_size"] = parts[1]
            specs["storage_avail"] = parts[3]
        else:
            specs["storage_size"] = "Unknown"
            specs["storage_avail"] = "Unknown"
            
        # GPU Info (nvidia-smi)
        stdin, stdout, stderr = ssh.exec_command("nvidia-smi --query-gpu=gpu_name,memory.total,driver_version --format=csv,noheader")
        gpu_out = stdout.read().decode("utf-8").strip()
        if gpu_out:
            gpu_parts = gpu_out.split(",")
            specs["gpu"] = gpu_parts[0].strip()
            specs["gpu_mem"] = gpu_parts[1].strip()
            specs["gpu_driver"] = gpu_parts[2].strip()
        else:
            specs["gpu"] = "No Nvidia GPU / Driver not loaded"
            specs["gpu_mem"] = "N/A"
            specs["gpu_driver"] = "N/A"
            
        # Docker
        stdin, stdout, stderr = ssh.exec_command("docker --version")
        specs["docker"] = stdout.read().decode("utf-8").strip() or "Not Installed"
        
        # Worker Service Status
        stdin, stdout, stderr = ssh.exec_command("systemctl is-active cluster-worker")
        service_status = stdout.read().decode("utf-8").strip()
        specs["service"] = "Active (running)" if service_status == "active" else f"Inactive ({service_status})"
        
        ssh.close()
        return specs
        
    except Exception as e:
        print(f"Error connecting to Worker {node['id']} ({node['ip']}): {e}")
        try:
            ssh.close()
        except:
            pass
        return None

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        
    workers = get_env_credentials()
    if not workers:
        print("No workers configured in .env file.")
        return
    
    print(f"Found {len(workers)} worker(s) in configuration. Starting audit...\n")
    
    results = {}
    for w in workers:
        specs = get_node_specs(w)
        if specs:
            results[w['id']] = specs
            
    print("\n================ AUDIT REPORT ================")
    for w_id, s in results.items():
        print(f"\nWORKER {w_id} SPECIFICATIONS:")
        print(f"  - Operating System : {s['os']} ({s['kernel']})")
        print(f"  - Processor (CPU)  : {s['cpu']}")
        print(f"  - Memory (RAM)     : Total {s['ram_total']} (Available {s['ram_available']})")
        print(f"  - Disk Storage     : Total {s['storage_size']} (Available {s['storage_avail']})")
        print(f"  - Graphics (GPU)   : {s['gpu']} (VRAM: {s['gpu_mem']}, Driver: {s['gpu_driver']})")
        print(f"  - Docker Support   : {s['docker']}")
        print(f"  - Cluster Service  : {s['service']}")
    print("==============================================\n")

if __name__ == "__main__":
    main()
