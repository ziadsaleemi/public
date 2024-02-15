from fastapi import FastAPI, HTTPException
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import json

app = FastAPI()

# Function to load vCenters from a JSON file
def load_vcenters_from_json(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

# Function to create an SSL context that does not verify SSL certificates
def get_ssl_context():
    context = None
    if hasattr(ssl, '_create_unverified_context'):
        context = ssl._create_unverified_context()
    return context

# Function to get all VMs from a vCenter
def get_vms_from_vcenter(vcenter):
    ssl_context = get_ssl_context()
    try:
        service_instance = SmartConnect(host=vcenter['server'], user=vcenter['user'], pwd=vcenter['password'], sslContext=ssl_context)
        content = service_instance.RetrieveContent()
        container = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        vms = container.view
        vm_list = [{'vm_name': vm.name, 'vm_id': vm._moId} for vm in vms]
        Disconnect(service_instance)
        return vm_list
    except Exception as e:
        print(f"Failed to connect to vCenter {vcenter['server']} with error: {e}")
        return []

# Function to save data to a JSON file
def save_data_to_json(file_path, data):
    with open(file_path, 'w') as file:
        json.dump(data, file, indent=4)

# Function to load data from a JSON file
def load_data_from_json(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

@app.get("/capture-vms")
async def capture_vms():
    vcenters_json_file = 'creds.json'  # Update this path
    output_json_file = 'vcenters.json'  # Specify the output file path
    vcenters = load_vcenters_from_json(vcenters_json_file)
    all_vms = {}
    for vcenter in vcenters:
        vms = get_vms_from_vcenter(vcenter)
        all_vms[vcenter['server']] = vms
    save_data_to_json(output_json_file, all_vms)
    return all_vms

# Endpoint to find the vCenter of a given VM, case-insensitively
@app.get("/find-vcenter/{vm_name}")
async def find_vcenter(vm_name: str):
    output_json_file = 'vcenters.json'  # Specify the path to your JSON file
    all_vms = load_data_from_json(output_json_file)
    
    vm_name_lower = vm_name.lower()  # Convert the input VM name to lowercase
    for vcenter, vms in all_vms.items():
        for vm in vms:
            if vm['vm_name'].lower() == vm_name_lower:  # Compare lowercased versions
                return {"vm_name": vm_name, "vcenter": vcenter}
    
    raise HTTPException(status_code=404, detail="VM not found")