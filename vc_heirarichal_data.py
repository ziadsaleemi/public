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


def collect_detailed_info(service_instance):
    content = service_instance.RetrieveContent()
    vcenter_info = []

    for datacenter in content.rootFolder.childEntity:
        if not hasattr(datacenter, 'hostFolder'):
            continue
        datacenter_info = {
            'datacenter_name': datacenter.name,
            'clusters': []
        }

        def traverse_folder(folder):
            for child in folder.childEntity:
                if isinstance(child, vim.ClusterComputeResource):
                    cluster_info = {
                        'cluster_name': child.name,
                        'datastores': [],
                        'networks': [],
                        'hosts': []
                    }

                    # Collect datastore information
                    for ds in child.datastore:
                        ds_info = {
                            'name': ds.name,
                            'capacity_gb': ds.summary.capacity / (1024**3),
                            'freeSpace_gb': ds.summary.freeSpace / (1024**3),
                            'type': ds.summary.type
                        }
                        cluster_info['datastores'].append(ds_info)

                    # Collect network information
                    for network in child.network:
                        net_info = {
                            'name': network.name,
                            'type': type(network).__name__
                        }
                        cluster_info['networks'].append(net_info)

                    # Collect host information
                    for host in child.host:
                        host_info = {
                            'host_name': host.name,
                            'cpu_capacity_ghz': host.hardware.cpuInfo.hz * host.hardware.cpuInfo.numCpuCores / 1e9,
                            'memory_capacity_gb': host.hardware.memorySize / (1024**3),
                            # Additional host details can be added here
                        }
                        cluster_info['hosts'].append(host_info)

                    datacenter_info['clusters'].append(cluster_info)
                elif isinstance(child, vim.Folder):
                    traverse_folder(child)

        traverse_folder(datacenter.hostFolder)
        vcenter_info.append(datacenter_info)

    return vcenter_info

@app.get("/collect-detailed-hierarchical-info")
async def collect_detailed_hierarchical_info():
    vcenters_json_file = 'creds.json'
    vcenters = json.load(open(vcenters_json_file))
    all_vcenter_info = {}

    for vcenter in vcenters:
        ssl_context = get_ssl_context()
        try:
            service_instance = SmartConnect(host=vcenter['server'], user=vcenter['user'], pwd=vcenter['password'], sslContext=ssl_context)
            vcenter_info = collect_detailed_info(service_instance)
            all_vcenter_info[vcenter['server']] = vcenter_info
            Disconnect(service_instance)
        except Exception as e:
            print(f"Failed to connect to vCenter {vcenter['server']} with error: {e}")
            all_vcenter_info[vcenter['server']] = 'Connection failed'

    # Optionally, save the collected data to a JSON file
    output_json_file = 'detailed_hierarchical_clusters.json'
    with open(output_json_file, 'w') as file:
        json.dump(all_vcenter_info, file, indent=4)

    return all_vcenter_info

@app.get("/query-hierarchical-info/")
async def query_hierarchical_info(datacenter_name: Optional[str] = None, 
                                  cluster_name: Optional[str] = None, 
                                  datastore_name: Optional[str] = None, 
                                  network_name: Optional[str] = None,
                                  host_name: Optional[str] = None):
    output_json_file = 'detailed_hierarchical_clusters.json'
    all_data = json.load(open(output_json_file))
    
    filtered_data = []

    for vcenter_data in all_data.values():
        for datacenter in vcenter_data:
            if datacenter_name and datacenter_name.lower() != datacenter['datacenter_name'].lower():
                continue
            
            filtered_datacenter = {"datacenter_name": datacenter['datacenter_name'], "clusters": []}
            for cluster in datacenter['clusters']:
                if cluster_name and cluster_name.lower() != cluster['cluster_name'].lower():
                    continue
                
                filtered_cluster = {key: value for key, value in cluster.items() if key in ['cluster_name', 'datastores', 'networks', 'hosts']}
                filtered_cluster['datastores'] = [ds for ds in cluster['datastores'] if not datastore_name or datastore_name.lower() in ds['name'].lower()]
                filtered_cluster['networks'] = [net for net in cluster['networks'] if not network_name or network_name.lower() in net['name'].lower()]
                filtered_cluster['hosts'] = [host for host in cluster['hosts'] if not host_name or host_name.lower() in host['host_name'].lower()]
                
                if not filtered_cluster['datastores'] and datastore_name:
                    continue
                if not filtered_cluster['networks'] and network_name:
                    continue
                if not filtered_cluster['hosts'] and host_name:
                    continue
                
                filtered_datacenter['clusters'].append(filtered_cluster)
            
            if filtered_datacenter['clusters']:
                filtered_data.append(filtered_datacenter)
    
    if not filtered_data:
        raise HTTPException(status_code=404, detail="No matching information found")
    
    return filtered_data