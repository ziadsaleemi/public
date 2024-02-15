from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import json

app = FastAPI()

def get_ssl_context():
    context = ssl._create_unverified_context()
    return context

def get_vm_details(vcenter):
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter['server'], user=vcenter['user'], pwd=vcenter['password'], sslContext=ssl_context)
        content = service_instance.RetrieveContent()
        container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        vms = container.view
        vm_details_list = []

        for vm in vms:
            vm_detail = {
                'vm_name': vm.summary.config.name,
                'networks': [],
                'storage': [],
                'datastores': [],
                'ip_addresses': []
            }

            # Network information
            for net in vm.network:
                if isinstance(net, vim.Network):
                    vm_detail['networks'].append(net.name)

            # Collecting all IP addresses
            ip_addresses = []
            for net_info in vm.guest.net:
                if net_info.ipConfig is not None and net_info.ipConfig.ipAddress:
                    for ip in net_info.ipConfig.ipAddress:
                        ip_addresses.append(ip.ipAddress)
            vm_detail['ip_addresses'] = ip_addresses

            # Storage information (Virtual Disks)
            for device in vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualDisk):
                    disk_detail = {
                        'label': device.deviceInfo.label,
                        'size_GB': device.capacityInKB / 1024 / 1024
                    }
                    vm_detail['storage'].append(disk_detail)

            # Datastore information
            for ds in vm.datastore:
                vm_detail['datastores'].append(ds.name)

            vm_details_list.append(vm_detail)

        Disconnect(service_instance)
        return vm_details_list
    except Exception as e:
        print(f"Failed to connect to vCenter {vcenter['server']} with error: {e}")
        return []

def get_cluster_info(service_instance):
    content = service_instance.RetrieveContent()
    cluster_info_list = []

    container = content.viewManager.CreateContainerView(content.rootFolder, [vim.ClusterComputeResource], True)
    clusters = container.view

    for cluster in clusters:
        # Initialize the cluster detail dictionary
        cluster_detail = {
            'cluster_name': cluster.name,
            'total_cpu': cluster.summary.totalCpu / 1000,  # Convert MHz to GHz
            'total_memory': cluster.summary.totalMemory / (1024**3),  # Convert Bytes to GB
            'available_cpu': cluster.summary.effectiveCpu / 1000,  # Convert MHz to GHz
            'available_memory': cluster.summary.effectiveMemory / 1024,  # Convert MB to GB
            'hosts': [],
            'datastores': [],
            'networks': []
        }

        # Collect datastore information
        for datastore in cluster.datastore:
            ds_detail = {
                'name': datastore.name,
                'capacity_gb': datastore.summary.capacity / (1024**3),  # Convert Bytes to GB
                'freeSpace_gb': datastore.summary.freeSpace / (1024**3),  # Convert Bytes to GB
                'type': datastore.summary.type,
                'accessible': datastore.summary.accessible
            }
            cluster_detail['datastores'].append(ds_detail)

        # Collect network information
        for network in cluster.network:
            net_detail = {
                'name': network.name,
                'type': type(network).__name__
            }
            cluster_detail['networks'].append(net_detail)

        # Collect host information (simplified for brevity)
        for host in cluster.host:
            datastore_capacity = 0
            datastore_free = 0
            for ds in host.datastore:
                datastore_info = ds.summary
                datastore_capacity += datastore_info.capacity
                datastore_free += datastore_info.freeSpace

            host_detail = {
                'host_name': host.name,
                'cpu_capacity': host.hardware.cpuInfo.hz * host.hardware.cpuInfo.numCpuCores / 1e9,  # GHz
                'memory_capacity': host.hardware.memorySize / (1024**3),  # GB
                'storage_capacity': datastore_capacity / (1024**3),  # Convert Bytes to GB
                'storage_free': datastore_free / (1024**3),  # Convert Bytes to GB
            }
            cluster_detail['hosts'].append(host_detail)

        cluster_info_list.append(cluster_detail)

    return cluster_info_list

def save_data_to_json(file_path, data):
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4)

def load_data_from_json(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)
    
@app.get("/capture-vm-details", tags=["VM"])
async def capture_vm_details():
    vcenters_json_file = 'creds.json'  # Update this path to your vCenters credentials file
    output_json_file = 'vm_details.json'  # The output file where VM details will be saved
    vcenters = json.load(open(vcenters_json_file))
    all_vm_details = {}

    for vcenter in vcenters:
        vm_details = get_vm_details(vcenter)
        all_vm_details[vcenter['server']] = vm_details

    save_data_to_json(output_json_file, all_vm_details)
    return {"message": "VM details captured successfully", "data": all_vm_details}

@app.get("/find-vcenter/{vm_name}", tags=["VM"])
async def find_vcenter(vm_name: str):
    output_json_file = 'vm_details.json'  # Specify the path to your JSON file
    all_vms = load_data_from_json(output_json_file)
    
    vm_name_lower = vm_name.lower()  # Convert the input VM name to lowercase
    for vcenter, vms in all_vms.items():
        for vm in vms:
            if vm['vm_name'].lower() == vm_name_lower:  # Compare lowercased versions
                return vm
    
    raise HTTPException(status_code=404, detail="VM not found")

@app.get("/collect-cluster-info", tags=["Clusters"])
async def collect_cluster_info():
    vcenters_json_file = 'creds.json'  # Path to your vCenters credentials file
    output_json_file = 'clusters.json'
    vcenters = json.load(open(vcenters_json_file))
    all_cluster_info = {}

    for vcenter in vcenters:
        ssl_context = get_ssl_context()
        try:
            service_instance = SmartConnect(host=vcenter['server'], user=vcenter['user'], pwd=vcenter['password'], sslContext=ssl_context)
            all_cluster_info[vcenter['server']] = get_cluster_info(service_instance)
            Disconnect(service_instance)
        except Exception as e:
            print(f"Failed to connect to vCenter {vcenter['server']} with error: {e}")
            all_cluster_info[vcenter['server']] = 'Connection failed'

        save_data_to_json(output_json_file, all_cluster_info)
    return all_cluster_info

# Endpoint to find the details of a given cluster by its name
@app.get("/query-cluster-info/")
async def query_cluster_info(cluster_name: Optional[str] = None, 
                             datastore_name: Optional[str] = None, 
                             host_name: Optional[str] = None, 
                             network_name: Optional[str] = None):
    output_json_file = 'clusters.json'
    all_clusters_info = json.load(open(output_json_file))
    
    filtered_clusters = []

    for vcenter, clusters in all_clusters_info.items():
        for cluster in clusters:
            # Filter by cluster name if specified
            if cluster_name and cluster_name.lower() != cluster['cluster_name'].lower():
                continue
            
            # Filter by datastore name if specified
            if datastore_name:
                datastores = [ds for ds in cluster['datastores'] if datastore_name.lower() in ds['name'].lower()]
                if not datastores:
                    continue
                else:
                    cluster['datastores'] = datastores
            
            # Filter by host name if specified
            if host_name:
                hosts = [host for host in cluster['hosts'] if host_name.lower() in host['host_name'].lower()]
                if not hosts:
                    continue
                else:
                    cluster['hosts'] = hosts
            
            # Filter by network name if specified
            if network_name:
                networks = [net for net in cluster['networks'] if network_name.lower() in net['name'].lower()]
                if not networks:
                    continue
                else:
                    cluster['networks'] = networks
            
            filtered_clusters.append({"vcenter": vcenter, "cluster_info": cluster})
    
    if not filtered_clusters:
        raise HTTPException(status_code=404, detail="No matching clusters found")
    
    return filtered_clusters

