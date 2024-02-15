from fastapi import FastAPI, HTTPException
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
    
@app.get("/capture-vm-details")
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

@app.get("/find-vcenter/{vm_name}")
async def find_vcenter(vm_name: str):
    output_json_file = 'vm_details.json'  # Specify the path to your JSON file
    all_vms = load_data_from_json(output_json_file)
    
    vm_name_lower = vm_name.lower()  # Convert the input VM name to lowercase
    for vcenter, vms in all_vms.items():
        for vm in vms:
            if vm['vm_name'].lower() == vm_name_lower:  # Compare lowercased versions
                return vm
    
    raise HTTPException(status_code=404, detail="VM not found")

