
from IPython.display import clear_output
import random
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from tqdm import tqdm
import matplotlib.pyplot as plt
import torch

from ssm.models import get_ssm_model_attention
from ssm.models import get_ssm_model_simple
from ssm.losses.ssm_loss import custom_loss

import numpy as np
from torch.utils.data import random_split

from ssm.utils import paired_preprocessing, visualize_attention_maps, subset_blind_spot_masking
from ssm.utils.config import get_config


from ssm.utils import paired_octa_preprocessing, paired_octa_preprocessing_binary

from ssm.models.unet.large_unet_old import LargeUNetAttention



def process_batch(dataloader, model, history, epoch, num_epochs, optimizer, loss_fn, loss_parameters, debug, n2v_weight, fast, visualise, mode='train'):
    running_loss = 0.0
    running_flow_loss = 0.0
    running_noise_loss = 0.0
    
    is_training = mode == 'train'
    
    progress_bar = tqdm(dataloader, desc=f"{mode.capitalize()} Epoch {epoch+1}/{num_epochs}")
    print(f"{mode.capitalize()}...")
    
    for batch_inputs, batch_targets in progress_bar:
        if is_training:
            model.train()
        else:
            model.eval()
        
        if is_training and optimizer:
            optimizer.zero_grad()
            
        with torch.set_grad_enabled(is_training):
            #outputs = model(masked_inputs)
            print(batch_inputs.shape)
            
            outputs = model(batch_inputs)

            flow_component = outputs['flow_component']
            noise_component = outputs['noise_component']

            if loss_fn.__name__ == 'custom_loss':
                total_loss = loss_fn(
                    flow_component, 
                    noise_component, 
                    batch_inputs, 
                    batch_targets, 
                    loss_parameters=loss_parameters, 
                    debug=debug)
            else:
                total_loss = loss_fn(
                    flow_component, 
                    batch_targets)

        if is_training and optimizer:
            # Debug parameter changes before step (first epoch only)
            if debug and epoch == 0:
                params_before = [p.clone().detach() for p in model.parameters()]
            
            total_loss.backward()
            optimizer.step()
            
            # Debug parameter changes after step (first epoch only)
            if debug and epoch == 0:
                params_after = [p.clone().detach() for p in model.parameters()]
                any_change = any(torch.any(b != a) for b, a in zip(params_before, params_after))
                print(f"Parameters changed: {any_change}")

        # Track losses
        noise_loss = 0  # Placeholder, adjust if you calculate this elsewhere
        running_loss += total_loss.item()
        running_flow_loss += total_loss.item()
        running_noise_loss += 0
        
        # Update progress bar
        progress_bar.set_postfix({
            'loss': total_loss.item(),
            'flow_loss': total_loss.item(),
            'noise_loss': noise_loss
        })

    avg_loss = running_loss / len(dataloader)
    avg_flow_loss = running_flow_loss / len(dataloader)
    avg_noise_loss = running_noise_loss / len(dataloader)
    
    if is_training:
        history['loss'].append(avg_loss)
        history['flow_loss'].append(avg_flow_loss)
        history['noise_loss'].append(avg_noise_loss)

    print(f"{mode.capitalize()} Epoch {epoch+1}/{num_epochs}, Loss: {avg_loss:.6f}, Flow Loss: {avg_flow_loss:.6f}, Noise Loss: {avg_noise_loss:.6f}")

    if visualise and mode == 'val':
        clear_output(wait=True)
        random.seed(epoch)
        random_idx = random.randint(0, batch_inputs.size(0)-1)
        
        visualize_progress(
            model, 
            batch_inputs[random_idx:random_idx+1], 
            batch_targets[random_idx:random_idx+1], 
            masked_tensor=None,
            epoch=epoch+1
        )
        plt.close()
        

    return avg_loss


from ssm.utils.data_utils.patch_processing import extract_patches, reconstruct_from_patches
from ssm.utils.eval_utils.visualise import visualize_progress_patch, visualize_progress

def process_batch_patch(dataloader, model, history, epoch, num_epochs, optimizer, loss_fn, loss_parameters, debug, n2v_weight, fast, visualise, mode='train'):
    running_loss = 0.0
    running_flow_loss = 0.0
    running_noise_loss = 0.0
    
    is_training = mode == 'train'
    patch_size = 128  # Set your desired patch size
    
    progress_bar = tqdm(dataloader, desc=f"{mode.capitalize()} Epoch {epoch+1}/{num_epochs}")
    print(f"{mode.capitalize()}...")
    
    for batch_inputs, batch_targets in progress_bar:
        if is_training:
            model.train()
        else:
            model.eval()
        
        if is_training and optimizer:
            optimizer.zero_grad()
        
        with torch.set_grad_enabled(is_training):
            # Extract patches from both inputs and targets
            input_patches, input_locations = extract_patches(batch_inputs, patch_size=patch_size)
            target_patches, _ = extract_patches(batch_targets, patch_size=patch_size)
            
            # Initialize containers for output patches
            flow_patches = []
            noise_patches = []
            
            # Process patches in smaller batches to avoid memory issues
            patch_batch_size = 16  # Adjust based on your GPU memory
            for i in range(0, len(input_patches), patch_batch_size):
                # Get current batch of patches
                batch_input_patch = input_patches[i:i+patch_batch_size]
                
                # Forward pass
                outputs = model(batch_input_patch)
                
                # Store output patches
                flow_patches.append(outputs['flow_component'])
                noise_patches.append(outputs['noise_component'])
            
            # Concatenate patch outputs
            flow_patches = torch.cat(flow_patches, dim=0)
            noise_patches = torch.cat(noise_patches, dim=0)
            
            # Calculate loss on patches
            if loss_fn.__name__ == 'custom_loss':
                total_loss = loss_fn(
                    flow_patches, 
                    noise_patches, 
                    input_patches, 
                    target_patches, 
                    loss_parameters=loss_parameters, 
                    debug=debug)
            else:
                total_loss = loss_fn(
                    flow_patches, 
                    target_patches)
            
        if is_training and optimizer:
            # Debug parameter changes before step (first epoch only)
            if debug and epoch == 0:
                params_before = [p.clone().detach() for p in model.parameters()]
            
            total_loss.backward()
            optimizer.step()
            
            # Debug parameter changes after step (first epoch only)
            if debug and epoch == 0:
                params_after = [p.clone().detach() for p in model.parameters()]
                any_change = any(torch.any(b != a) for b, a in zip(params_before, params_after))
                print(f"Parameters changed: {any_change}")

        # Track losses
        noise_loss = 0  # Placeholder
        running_loss += total_loss.item()
        running_flow_loss += total_loss.item()
        running_noise_loss += 0
        
        # Update progress bar
        progress_bar.set_postfix({
            'loss': total_loss.item(),
            'flow_loss': total_loss.item(),
            'noise_loss': noise_loss
        })

    avg_loss = running_loss / len(dataloader)
    avg_flow_loss = running_flow_loss / len(dataloader)
    avg_noise_loss = running_noise_loss / len(dataloader)
    
    if is_training:
        history['loss'].append(avg_loss)
        history['flow_loss'].append(avg_flow_loss)
        history['noise_loss'].append(avg_noise_loss)

    print(f"{mode.capitalize()} Epoch {epoch+1}/{num_epochs}, Loss: {avg_loss:.6f}, Flow Loss: {avg_flow_loss:.6f}, Noise Loss: {avg_noise_loss:.6f}")

    if visualise and mode == 'val':
        clear_output(wait=True)
        random.seed(epoch)
        random_idx = random.randint(0, batch_inputs.size(0)-1)
        
        # Use a single image for visualization
        single_input = batch_inputs[random_idx:random_idx+1]
        single_target = batch_targets[random_idx:random_idx+1]
        
        # Extract patches and process
        with torch.no_grad():
            vis_patches, vis_locations = extract_patches(single_input, patch_size=patch_size)
            outputs = model(vis_patches)
            
            # Reconstruct full image from patches
            flow_full = reconstruct_from_patches(
                outputs['flow_component'], 
                vis_locations, 
                single_input.shape,
                patch_size=patch_size
            )
            
            noise_full = reconstruct_from_patches(
                outputs['noise_component'], 
                vis_locations, 
                single_input.shape,
                patch_size=patch_size
            )
        
        visualize_progress_patch(
            model, 
            single_input, 
            single_target, 
            masked_tensor=None,
            epoch=epoch+1,
            predicted_flow=flow_full,
            predicted_noise=noise_full
        )
        plt.close()

    return avg_loss

def train(train_dataloader, val_dataloader, checkpoint, checkpoint_path, model, history, optimizer, 
          set_epoch, num_epochs, loss_fn, loss_parameters, debug, n2v_weight, fast, visualise):
    
    # Setup checkpoint paths
    last_checkpoint = checkpoint_path.replace('.pth', f'_last.pth')
    best_checkpoint = checkpoint_path.replace('.pth', f'_best.pth')
    
    # Get best loss from checkpoint if available
    best_loss = checkpoint['best_loss'] if 'best_loss' in checkpoint else float('inf')
    best_epoch = checkpoint['epoch'] if 'epoch' in checkpoint else 0
    
    # Enable anomaly detection for debugging if needed
    torch.autograd.set_detect_anomaly(True)
    
    # Add validation loss to history if not present
    if 'val_loss' not in history:
        history['val_loss'] = []
    
    for epoch in range(set_epoch, num_epochs):
        print(f"Epoch {epoch+1}/{num_epochs}")
        
        
        train_loss = process_batch(
            train_dataloader, model, history, 
            epoch, num_epochs, optimizer, 
            loss_fn, loss_parameters, debug, 
            n2v_weight, fast, visualise,
            mode='train'
        )
        
        # Validation phase
        val_loss = process_batch(
            val_dataloader, model, history, 
            epoch, num_epochs, None,  # No optimizer for validation 
            loss_fn, loss_parameters, False,  # No debug during validation
            n2v_weight, fast, visualise,
            mode='val'
        )
        
        history['val_loss'].append(val_loss)
        
        if val_loss < best_loss:
            best_loss = val_loss
            best_epoch = epoch + 1
            print(f"New best model found at epoch {best_epoch} with validation loss {best_loss:.6f}")
            
            checkpoint = {
                'epoch': best_epoch,
                'model_state_dict': model.state_dict(),
                'best_loss': best_loss,
                'train_loss': train_loss,
                'val_loss': val_loss,
                'optimizer_state_dict': optimizer.state_dict(),
                'history': history,
            }
            torch.save(checkpoint, best_checkpoint)
            print(f"Best model checkpoint saved at {best_checkpoint}")

        # Save last checkpoint
        checkpoint = {
            'epoch': epoch + 1,  # Save epoch + 1 so we can resume from next epoch
            'model_state_dict': model.state_dict(),
            'best_loss': best_loss,
            'best_epoch': best_epoch,
            'train_loss': train_loss,
            'val_loss': val_loss,
            'optimizer_state_dict': optimizer.state_dict(),
            'history': history
        }
        
        torch.save(checkpoint, last_checkpoint)
        print(f"Latest model checkpoint saved at {last_checkpoint}")
    
    return model, history

def get_loaders(dataset, batch_size, val_split=0.2, device='cuda', seed=42):

    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    
    input_tensors = []
    target_tensors = []
    
    for patient in dataset:
        patient_data = dataset[patient]
        for input_img, target_img in patient_data:
            # Convert to tensor and add channel dimension if needed
            if len(input_img.shape) == 2:
                input_tensor = torch.from_numpy(input_img).float().unsqueeze(0)
                target_tensor = torch.from_numpy(target_img).float().unsqueeze(0)
            else:
                input_tensor = torch.from_numpy(input_img).float()
                target_tensor = torch.from_numpy(target_img).float()
                
            input_tensors.append(input_tensor)
            target_tensors.append(target_tensor)
    
    # Stack all tensors
    #inputs = torch.stack(input_tensors).to(device)
    #targets = torch.stack(target_tensors).to(device)

    inputs = torch.stack(input_tensors).permute(0, 3, 1, 2).to(device)
    targets = torch.stack(target_tensors).permute(0, 3, 1, 2).to(device)
    
    full_dataset = TensorDataset(inputs, targets)

    dataset_size = len(full_dataset)
    val_size = int(val_split * dataset_size)
    train_size = dataset_size - val_size
    
    train_dataset, val_dataset = random_split(full_dataset, [train_size, val_size])
    
    # Create dataloaders
    train_loader = DataLoader(
        train_dataset, 
        batch_size=batch_size, 
        shuffle=True
    )
    
    val_loader = DataLoader(
        val_dataset, 
        batch_size=batch_size,
        shuffle=False  # No need to shuffle validation data
    )
    
    print(f"Dataset split: {train_size} training samples, {val_size} validation samples")
    
    return train_loader, val_loader


def train_speckle_separation_module(train_config, loss_fn, loss_name):

    device = train_config['device']

    n_patients = train_config['n_patients']

    start = train_config['start']

    n_images_per_patient = train_config['n_images']

    #dataset = paired_octa_preprocessing(start, n_patients, n_images_per_patient, n_neighbours = 10, threshold=65, sample=False, post_process_size=10)
    dataset = paired_octa_preprocessing_binary(start, n_patients, n_images_per_patient, n_neighbours = 4, threshold=99, sample=False, post_process_size=2)
    #dataset = process_octa_segmentation_batch_patches(start, n_patients, n_images_per_patient, n_neighbours = 10, threshold=85, sample=False, post_process_size=10)

    print(f"Dataset size: {len(dataset)} patients")

    batch_size = train_config['batch_size']
    
    #dataloader = get_loaders(dataset, batch_size, device)
    train_loader, val_loader = get_loaders(dataset, batch_size, val_split=0.2, device=device)
    
    history = {
        'loss': [],
        'flow_loss': [],
        'noise_loss': []
    }

    learning_rate = train_config['learning_rate']
    num_epochs = train_config['num_epochs']
    
    set_epoch = 0

    base_checkpoint_path = train_config['checkpoint'].format(loss_fn=loss_name)

    model_name = train_config['model_name']

    if model_name == 'SSMSimple':
        if train_config['load_model']:
            checkpoint_path = train_config['checkpoint'].format(loss_fn=loss_name)
            model = get_ssm_model_simple(checkpoint_path=checkpoint_path)
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            best_loss = checkpoint['best_loss']
            set_epoch = checkpoint['epoch']
            history = checkpoint['history']
            num_epochs = num_epochs + set_epoch
            print(f"Model loaded from {checkpoint_path} at epoch {set_epoch} with loss {best_loss:.6f}")
        else:
            model = get_ssm_model_simple(checkpoint_path=None)
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            best_loss = float('inf')
            set_epoch = 0
    
    if model_name == 'SSMAttention':
        if train_config['load_model']:
            checkpoint_path = train_config['checkpoint'].format(loss_fn=loss_name)
            model = get_ssm_model_attention(checkpoint_path=checkpoint_path)
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            best_loss = checkpoint['best_loss']
            set_epoch = checkpoint['epoch']
            history = checkpoint['history']
            num_epochs = num_epochs + set_epoch
            print(f"Model loaded from {checkpoint_path} at epoch {set_epoch} with loss {best_loss:.6f}")
        else:
            model = get_ssm_model_attention(checkpoint_path=None)
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            best_loss = float('inf')
            set_epoch = 0

    if model_name == 'UNet':
        checkpoint_path = train_config['checkpoint'].format(loss_fn=loss_name)
        print("Loading UNet model...")
        if train_config['load_model']:
            checkpoint = torch.load(checkpoint_path, map_location='cpu')
            model = LargeUNetAttention(in_channels=1, out_channels=1).to(device)
            model.load_state_dict(checkpoint['model_state_dict'])
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            best_loss = checkpoint['best_loss']
            set_epoch = checkpoint['epoch']
            history = checkpoint['history']
            num_epochs = num_epochs + set_epoch
            print(f"Model loaded from {checkpoint_path} at epoch {set_epoch} with loss {best_loss:.6f}")
        else:
            model = LargeUNetAttention()
            model.to(device)
            optimizer = optim.Adam(model.parameters(), lr=learning_rate)
            best_loss = float('inf')
            set_epoch = 0
    
    print(f"Model: {model_name}")
    checkpoint = {
        'epoch': set_epoch,
        'model_state_dict': model.state_dict(),
        'best_loss': best_loss,
        'optimizer_state_dict': optimizer.state_dict(),  # optional, but useful
        'history': history,
        }

    n2v_weight = train_config['n2v_weight']
    #loss_fn = train_config['loss_fn']
    debug = train_config['debug']
    fast = train_config['fast']
    visualise = train_config['visualise']
    loss_parameters = train_config['loss_parameters']

    train(train_loader, val_loader, checkpoint, base_checkpoint_path, model, history, 
          optimizer, set_epoch, num_epochs, 
          loss_fn, loss_parameters, debug, 
          n2v_weight, fast, visualise)
    
#############

def train_ssm():


    config_path = r"C:\Users\CL-11\OneDrive\Repos\OCTDenoisingFinal\configs\ssm_config.yaml"

    config = get_config(config_path)

    loss_names = ['custom_loss', 'mse']
    loss_name = 'custom_loss'  # 'mse' or 'custom_loss'

    if loss_name == 'mse':
        loss_fn = None
    else:
        loss_fn = custom_loss

    train_speckle_separation_module(config['training'], loss_fn, loss_name)

    #from ssm.models.ssm_attention import get_ssm_model
    #from torchviz import make_dot
    #import torch

    #model = get_ssm_model(checkpoint=None)
    #x = torch.randn(1, 1, 256, 256)  # Example input tensor
    #outputs = model(x)  # Forward pass through the model

    # Get both outputs
    #flow_output = outputs['flow_component']
    #noise_output = outputs['noise_component']

    # Visualize both outputs together
    #both_outputs = (flow_output, noise_output)
    #graph = make_dot(both_outputs, params=dict(model.named_parameters()))
    #graph.render("ssm_model", format="png")

if __name__ == "__main__":
    train_ssm()