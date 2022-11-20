#!/usr/bin/env python
# coding: utf-8

# # Initialization

# * implement unifyGNN+Contrastive
# * try different noisy and augmentation
# * try against with/without unnoticeable 

# In[1]:


#!/usr/bin/env python
# coding: utf-8

# In[1]: 

import imp
import time
import argparse
import numpy as np
import torch

from torch_geometric.datasets import Planetoid,Reddit2,Flickr,PPI


# from torch_geometric.loader import DataLoader
# from help_funcs import prune_unrelated_edge,prune_unrelated_edge_isolated
import scipy.sparse as sp

# Training settings
parser = argparse.ArgumentParser()
parser.add_argument('--debug', action='store_true',
        default=True, help='debug mode')
parser.add_argument('--no-cuda', action='store_true', default=False,
                    help='Disables CUDA training.')
parser.add_argument('--seed', type=int, default=10, help='Random seed.')
parser.add_argument('--model', type=str, default='GCN', help='model',
                    choices=['GCN','GAT','GraphSage','GIN'])
parser.add_argument('--dataset', type=str, default='Cora', 
                    help='Dataset',
                    choices=['Cora','Citeseer','Pubmed','PPI','Flickr','ogbn-arxiv','Reddit','Reddit2','Yelp'])
parser.add_argument('--train_lr', type=float, default=0.01,
                    help='Initial learning rate.')
parser.add_argument('--weight_decay', type=float, default=5e-4,
                    help='Weight decay (L2 loss on parameters).')
parser.add_argument('--hidden', type=int, default=128,
                    help='Number of hidden units.')
parser.add_argument('--proj_hidden', type=int, default=128,
                    help='Number of hidden units in MLP.')
parser.add_argument('--thrd', type=float, default=0.5)
parser.add_argument('--target_class', type=int, default=0)
parser.add_argument('--dropout', type=float, default=0.5,
                    help='Dropout rate (1 - keep probability).')
parser.add_argument('--epochs', type=int,  default=200, help='Number of epochs to train benign and backdoor model.')
parser.add_argument('--trojan_epochs', type=int,  default=400, help='Number of epochs to train trigger generator.')
parser.add_argument('--inner', type=int,  default=1, help='Number of inner')
parser.add_argument('--temperature', type=float,  default=0.5, help='Temperature')
# backdoor setting
parser.add_argument('--lr', type=float, default=0.01,
                    help='Initial learning rate.')
parser.add_argument('--trigger_size', type=int, default=3,
                    help='tirgger_size')
parser.add_argument('--use_vs_number', action='store_true', default=True,
                    help="if use detailed number to decide Vs")
parser.add_argument('--vs_ratio', type=float, default=0,
                    help="ratio of poisoning nodes relative to the full graph")
parser.add_argument('--vs_number', type=int, default=160,
                    help="number of poisoning nodes relative to the full graph")
# defense setting
parser.add_argument('--defense_mode', type=str, default="none",
                    choices=['prune', 'isolate', 'none'],
                    help="Mode of defense")
parser.add_argument('--prune_thr', type=float, default=0.2,
                    help="Threshold of prunning edges")
parser.add_argument('--target_loss_weight', type=float, default=1,
                    help="Weight of optimize outter trigger generator")
parser.add_argument('--homo_loss_weight', type=float, default=0,
                    help="Weight of optimize similarity loss")
parser.add_argument('--homo_boost_thrd', type=float, default=0.5,
                    help="Threshold of increase similarity")
# attack setting
parser.add_argument('--dis_weight', type=float, default=1,
                    help="Weight of cluster distance")
parser.add_argument('--selection_method', type=str, default='cluster_degree',
                    choices=['loss','conf','cluster','none','cluster_degree'],
                    help='Method to select idx_attach for training trojan model (none means randomly select)')
parser.add_argument('--test_model', type=str, default='GCN',
                    choices=['GCN','GAT','GraphSage','GIN'],
                    help='Model used to attack')
parser.add_argument('--evaluate_mode', type=str, default='1by1',
                    choices=['overall','1by1'],
                    help='Model used to attack')
# GPU setting
parser.add_argument('--device_id', type=int, default=3,
                    help="Threshold of prunning edges")
# GRACE setting
parser.add_argument('--gpu_id', type=int, default=0)
parser.add_argument('--config', type=str, default='config.yaml')
# args = parser.parse_args()
args = parser.parse_known_args()[0]
args.cuda =  not args.no_cuda and torch.cuda.is_available()
device = torch.device(('cuda:{}' if torch.cuda.is_available() else 'cpu').format(args.device_id))

np.random.seed(args.seed)
torch.manual_seed(args.seed)
torch.cuda.manual_seed(args.seed)
print(args)
#%%
from torch_geometric.utils import to_undirected
import torch_geometric.transforms as T
transform = T.Compose([T.NormalizeFeatures()])

if(args.dataset == 'Cora' or args.dataset == 'Citeseer' or args.dataset == 'Pubmed'):
    dataset = Planetoid(root='./data/',                         name=args.dataset,                        transform=transform)
elif(args.dataset == 'Flickr'):
    dataset = Flickr(root='./data/Flickr/',                     transform=transform)
elif(args.dataset == 'Reddit2'):
    dataset = Reddit2(root='./data/Reddit2/',                     transform=transform)
elif(args.dataset == 'ogbn-arxiv'):
    from ogb.nodeproppred import PygNodePropPredDataset
    # Download and process data at './dataset/ogbg_molhiv/'
    dataset = PygNodePropPredDataset(name = 'ogbn-arxiv', root='./data/')
    split_idx = dataset.get_idx_split() 

data = dataset[0].to(device)

if(args.dataset == 'ogbn-arxiv'):
    nNode = data.x.shape[0]
    setattr(data,'train_mask',torch.zeros(nNode, dtype=torch.bool).to(device))
    # dataset[0].train_mask = torch.zeros(nEdge, dtype=torch.bool).to(device)
    data.val_mask = torch.zeros(nNode, dtype=torch.bool).to(device)
    data.test_mask = torch.zeros(nNode, dtype=torch.bool).to(device)
    data.y = data.y.squeeze(1)
# we build our own train test split 
#%% 
from utils import get_split
data, idx_train, idx_val, idx_clean_test, idx_atk = get_split(args,data,device)

from torch_geometric.utils import to_undirected
from utils import subgraph
data.edge_index = to_undirected(data.edge_index)
train_edge_index,_, edge_mask = subgraph(torch.bitwise_not(data.test_mask),data.edge_index,relabel_nodes=False)
mask_edge_index = data.edge_index[:,torch.bitwise_not(edge_mask)]

# filter out the unlabeled nodes except from training nodes and testing nodes, nonzero() is to get index, flatten is to get 1-d tensor
unlabeled_idx = (torch.bitwise_not(data.test_mask)&torch.bitwise_not(data.train_mask)).nonzero().flatten()


# In[2]:


import os.path as osp
import random
from time import perf_counter as t
import yaml
from yaml import SafeLoader

import torch
import torch_geometric.transforms as T
import torch.nn.functional as F
import torch.nn as nn
from torch_geometric.datasets import Planetoid, CitationFull
from torch_geometric.utils import dropout_adj
from torch_geometric.nn import GCNConv

from model import Encoder, Model, drop_feature
from eval import label_classification

from construct_graph import construct_noisy_graph,construct_augmentation, construct_augmentation_1

def train(model: Model, x, edge_index):
    model.train()
    optimizer.zero_grad()
    edge_index_1 = dropout_adj(edge_index, p=drop_edge_rate_1)[0]
    edge_index_2 = dropout_adj(edge_index, p=drop_edge_rate_2)[0]
    x_1 = drop_feature(x, drop_feature_rate_1)
    x_2 = drop_feature(x, drop_feature_rate_2)

    z1 = model(x_1, edge_index_1)
    z2 = model(x_2, edge_index_2)

    loss = model.loss(z1, z2, batch_size=0)
    loss.backward()
    optimizer.step()

    return loss.item()
def train_1(model: Model, optimizer, x, edge_index,edge_weights = None,seen_node_idx = None):
    model.train()
    optimizer.zero_grad()
    edge_index_1,x_1,edge_index_2,x_2 = construct_augmentation_1(x, edge_index, None)
    # print(edge_index_1,edge_index_2)
    # edge_index_1 = dropout_adj(edge_index, p=drop_edge_rate_1)[0]
    # edge_index_2 = dropout_adj(edge_index, p=drop_edge_rate_2)[0]
    # x_1 = drop_feature(x, drop_feature_rate_1)
    # x_2 = drop_feature(x, drop_feature_rate_2)

    z1 = model(x_1, edge_index_1)
    z2 = model(x_2, edge_index_2)
    if(seen_node_idx!=None):
        loss = model.loss(z1[seen_node_idx], z2[seen_node_idx], batch_size=0)
    else:
        loss = model.loss(z1, z2, batch_size=0)
    loss.backward()
    optimizer.step()

    return loss.item()

def test(model: Model, x, edge_index, y, idx_train, idx_test, final=False):
    model.eval()
    z = model(x, edge_index)

    results = label_classification(z, y, idx_train, idx_test)
    return results['F1Mi']['mean'],results['F1Ma']['mean']
# parser = argparse.ArgumentParser()
# parser.add_argument('--dataset', type=str, default='Cora')
# parser.add_argument('--gpu_id', type=int, default=0)
# parser.add_argument('--config', type=str, default='config.yaml')
# args = parser.parse_known_args()[0]

assert args.gpu_id in range(0, 8)
# torch.cuda.set_device(args.gpu_id)

config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]

# torch.manual_seed(config['seed'])
# random.seed(config['seed'])
learning_rate = config['learning_rate']
num_hidden = config['num_hidden']
num_proj_hidden = config['num_proj_hidden']
activation = ({'relu': F.relu, 'prelu': nn.PReLU()})[config['activation']]
base_model = ({'GCNConv': GCNConv})[config['base_model']]
num_layers = config['num_layers']

drop_edge_rate_1 = config['drop_edge_rate_1']
drop_edge_rate_2 = config['drop_edge_rate_2']
drop_feature_rate_1 = config['drop_feature_rate_1']
drop_feature_rate_2 = config['drop_feature_rate_2']
tau = config['tau']
num_epochs = config['num_epochs']
weight_decay = config['weight_decay']

data = data.to(device)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.3,mode='random_noise')
noisy_data = noisy_data.to(device)
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)


# In[29]:


from torch_geometric.utils import to_dense_adj, dense_to_sparse
from torch_geometric.data import Data, InMemoryDataset

import os

import numpy as np
from scipy.linalg import expm

import torch
from torch_geometric.data import Data, InMemoryDataset
from torch_geometric.datasets import Planetoid, Amazon, Coauthor

# from seeds import development_seed
DATA_PATH = './data/'

def get_dataset(name: str, use_lcc: bool = True) -> InMemoryDataset:
    path = os.path.join(DATA_PATH, name)
    if name in ['Cora', 'Citeseer', 'Pubmed']:
        dataset = Planetoid(path, name)
    elif name in ['Computers', 'Photo']:
        dataset = Amazon(path, name)
    elif name == 'CoauthorCS':
        dataset = Coauthor(path, 'CS')
    else:
        raise Exception('Unknown dataset.')

    if use_lcc:
        lcc = get_largest_connected_component(dataset)

        x_new = dataset.data.x[lcc]
        y_new = dataset.data.y[lcc]

        row, col = dataset.data.edge_index.numpy()
        edges = [[i, j] for i, j in zip(row, col) if i in lcc and j in lcc]
        edges = remap_edges(edges, get_node_mapper(lcc))
        
        data = Data(
            x=x_new,
            edge_index=torch.LongTensor(edges),
            y=y_new,
            train_mask=torch.zeros(y_new.size()[0], dtype=torch.bool),
            test_mask=torch.zeros(y_new.size()[0], dtype=torch.bool),
            val_mask=torch.zeros(y_new.size()[0], dtype=torch.bool)
        )
        dataset.data = data

    return dataset


def get_component(dataset: InMemoryDataset, start: int = 0) -> set:
    visited_nodes = set()
    queued_nodes = set([start])
    row, col = dataset.data.edge_index.numpy()
    while queued_nodes:
        current_node = queued_nodes.pop()
        visited_nodes.update([current_node])
        neighbors = col[np.where(row == current_node)[0]]
        neighbors = [n for n in neighbors if n not in visited_nodes and n not in queued_nodes]
        queued_nodes.update(neighbors)
    return visited_nodes


def get_largest_connected_component(dataset: InMemoryDataset) -> np.ndarray:
    remaining_nodes = set(range(dataset.data.x.shape[0]))
    comps = []
    while remaining_nodes:
        start = min(remaining_nodes)
        comp = get_component(dataset, start)
        comps.append(comp)
        remaining_nodes = remaining_nodes.difference(comp)
    return np.array(list(comps[np.argmax(list(map(len, comps)))]))


def get_node_mapper(lcc: np.ndarray) -> dict:
    mapper = {}
    counter = 0
    for node in lcc:
        mapper[node] = counter
        counter += 1
    return mapper


def remap_edges(edges: list, mapper: dict) -> list:
    row = [e[0] for e in edges]
    col = [e[1] for e in edges]
    row = list(map(lambda x: mapper[x], row))
    col = list(map(lambda x: mapper[x], col))
    return [row, col]


def get_adj_matrix(data) -> np.ndarray:
    num_nodes = data.x.shape[0]
    adj_matrix = np.zeros(shape=(num_nodes, num_nodes))
    for i, j in zip(data.edge_index[0], data.edge_index[1]):
        adj_matrix[i, j] = 1.
    return adj_matrix


def get_ppr_matrix(
        adj_matrix: np.ndarray,
        alpha: float = 0.1) -> np.ndarray:
    num_nodes = adj_matrix.shape[0]
    A_tilde = adj_matrix + np.eye(num_nodes)
    D_tilde = np.diag(1/np.sqrt(A_tilde.sum(axis=1)))
    H = D_tilde @ A_tilde @ D_tilde
    return alpha * np.linalg.inv(np.eye(num_nodes) - (1 - alpha) * H)


def get_heat_matrix(
        adj_matrix: np.ndarray,
        t: float = 5.0) -> np.ndarray:
    num_nodes = adj_matrix.shape[0]
    A_tilde = adj_matrix + np.eye(num_nodes)
    D_tilde = np.diag(1/np.sqrt(A_tilde.sum(axis=1)))
    H = D_tilde @ A_tilde @ D_tilde
    return expm(-t * (np.eye(num_nodes) - H))


def get_top_k_matrix(A: np.ndarray, k: int = 128) -> np.ndarray:
    num_nodes = A.shape[0]
    row_idx = np.arange(num_nodes)
    A[A.argsort(axis=0)[:num_nodes - k], row_idx] = 0.
    norm = A.sum(axis=0)
    norm[norm <= 0] = 1 # avoid dividing by zero
    return A/norm


def get_clipped_matrix(A: np.ndarray, eps: float = 0.01) -> np.ndarray:
    num_nodes = A.shape[0]
    A[A < eps] = 0.
    norm = A.sum(axis=0)
    norm[norm <= 0] = 1 # avoid dividing by zero
    return A/norm


def set_train_val_test_split(
        seed: int,
        data: Data,
        num_development: int = 1500,
        num_per_class: int = 20) -> Data:
    rnd_state = np.random.RandomState(development_seed)
    num_nodes = data.y.shape[0]
    development_idx = rnd_state.choice(num_nodes, num_development, replace=False)
    test_idx = [i for i in np.arange(num_nodes) if i not in development_idx]

    train_idx = []
    rnd_state = np.random.RandomState(seed)
    for c in range(data.y.max() + 1):
        class_idx = development_idx[np.where(data.y[development_idx].cpu() == c)[0]]
        train_idx.extend(rnd_state.choice(class_idx, num_per_class, replace=False))

    val_idx = [i for i in development_idx if i not in train_idx]

    def get_mask(idx):
        mask = torch.zeros(num_nodes, dtype=torch.bool)
        mask[idx] = 1
        return mask

    data.train_mask = get_mask(train_idx)
    data.val_mask = get_mask(val_idx)
    data.test_mask = get_mask(test_idx)

    return data

class PPRDataset(InMemoryDataset):
    """
    Dataset preprocessed with GDC using PPR diffusion.
    Note that this implementations is not scalable
    since we directly invert the adjacency matrix.
    """
    def __init__(self,noisy_data,
                 name: str = 'Cora',
                 use_lcc: bool = True,
                 alpha: float = 0.1,
                 k: int = 16,
                 eps: float = None):
        self.name = name
        self.use_lcc = use_lcc
        self.alpha = alpha
        self.k = k
        self.eps = eps
        self.noisy_data = noisy_data

        super(PPRDataset, self).__init__(DATA_PATH)
        self.data, self.slices = torch.load(self.processed_paths[0])

    @property
    def raw_file_names(self) -> list:
        return []

    @property
    def processed_file_names(self) -> list:
        return [str(self) + '.pt']

    def download(self):
        pass

    def process(self):
        # base = get_dataset(name=self.name, use_lcc=self.use_lcc)
        # generate adjacency matrix from sparse representation
        adj_matrix = get_adj_matrix(self.noisy_data)
        # obtain exact PPR matrix
        ppr_matrix = get_ppr_matrix(adj_matrix,
                                        alpha=self.alpha)

        if self.k:
            print(f'Selecting top {self.k} edges per node.')
            ppr_matrix = get_top_k_matrix(ppr_matrix, k=self.k)
        elif self.eps:
            print(f'Selecting edges with weight greater than {self.eps}.')
            ppr_matrix = get_clipped_matrix(ppr_matrix, eps=self.eps)
        else:
            raise ValueError

        # create PyG Data object
        edges_i = []
        edges_j = []
        edge_attr = []
        for i, row in enumerate(ppr_matrix):
            for j in np.where(row > 0)[0]:
                edges_i.append(i)
                edges_j.append(j)
                edge_attr.append(ppr_matrix[i, j])
        edge_index = [edges_i, edges_j]

        data = Data(
            x=self.noisy_data.x,
            edge_index=torch.LongTensor(edge_index),
            edge_attr=torch.FloatTensor(edge_attr),
            y=self.noisy_data.y,
            train_mask=torch.zeros(self.noisy_data.train_mask.size()[0], dtype=torch.bool),
            test_mask=torch.zeros(self.noisy_data.test_mask.size()[0], dtype=torch.bool),
            val_mask=torch.zeros(self.noisy_data.val_mask.size()[0], dtype=torch.bool)
        )

        data, slices = self.collate([data])
        torch.save((data, slices), self.processed_paths[0])

    def __str__(self) -> str:
        return f'{self.name}_ppr_alpha={self.alpha}_k={self.k}_eps={self.eps}_lcc={self.use_lcc}'
# gdc(A,1,1)
# import copy 
# from model import UnifyModel
# from models.construct import model_construct
# data = data.to(device)
# config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]
# num_epochs = config['num_epochs']
# learning_rate = config['learning_rate']
# weight_decay = config['weight_decay']
# args.seed = config['seed']
# args.cont_batch_size = config['cont_batch_size']
# args.cont_weight = config['cont_weight']
# args.add_edge_rate_1 = config['add_edge_rate_1']
# args.add_edge_rate_2 = config['add_edge_rate_2']
# args.drop_edge_rate_1 = config['drop_edge_rate_1']
# args.drop_edge_rate_2 = config['drop_edge_rate_2']
# args.drop_feat_rate_1 = config['drop_feature_rate_1']
# args.drop_feat_rate_2 = config['drop_feature_rate_2']
# # args.add_edge_rate_1 = 0
# # args.add_edge_rate_2 = 0
# # args.drop_edge_rate_1 = 0.3
# # args.drop_edge_rate_2 = 0.5
# # args.drop_feat_rate_1 = 0.4
# # args.drop_feat_rate_2 = 0.4
# num_class = int(data.y.max()+1)

# noisy_data = construct_noisy_graph(data,perturb_ratio=0.1,mode='random_noise')
# noisy_data = noisy_data.to(device)

# diff_dataset = PPRDataset(noisy_data)
# diff_noisy_data = diff_dataset.data
# # diff_dataset.processed_paths
# diff_noisy_data


# In[11]:


diff_noisy_data


# ## Graph Structure Noise

# ### Transductive

# In[12]:


idx_overall_test = (torch.bitwise_not(data.train_mask)&torch.bitwise_not(data.val_mask)).nonzero().flatten()
'''Transductive'''
import copy 
encoder = Encoder(dataset.num_features, num_hidden, activation,
                        base_model=base_model, k=num_layers).to(device)
model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
optimizer = torch.optim.Adam(
    model.parameters(), lr=learning_rate, weight_decay=weight_decay)
from models.GCN import GCN
from models.construct import model_construct
gnn_model = model_construct(args,'GCN',data,device)

model_origin = copy.deepcopy(model)
encoder_origin = copy.deepcopy(encoder)
optimizer_origin = copy.deepcopy(optimizer)
gnn_model_origin = copy.deepcopy(gnn_model)

# seeds = [args.seed]
final_cl_acc = []
final_gnn_acc = []
print("=== Raw graph ===")
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    print("Learn node representations via contrastive learning")
    model = copy.deepcopy(model_origin)
    encoder = copy.deepcopy(encoder_origin)
    # optimizer = copy.deepcopy(optimizer_origin)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    start = t()
    prev = start
    for epoch in range(1, num_epochs + 1):
        # loss = train_(model, data.x, data.edge_index)
        loss = train_1(model, optimizer, data.x, data.edge_index)

        now = t()
        if(epoch%10 == 0):
            print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
                    f'this epoch {now - prev:.4f}, total {now - start:.4f}')
        prev = now

    # print("=== Test ===")
    f1mi,f1ma = test(model, data.x, data.edge_index, data.y, idx_train, idx_overall_test, final=True)
    final_cl_acc.append(f1mi)
    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    gnn_model = copy.deepcopy(gnn_model_origin)
    gnn_model.fit(data.x, data.edge_index, None, data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(data.x,data.edge_index,data.edge_weight,data.y,idx_overall_test)
    final_gnn_acc.append(clean_acc)
    print("accuracy of clean model on clean test nodes: {:.4f}".format(clean_acc))
print("=== Noisy graph ===")
final_cl_acc_noisy = []
final_gnn_acc_noisy = []
for seed in seeds:
    print("Learn node representations via contrastive learning")
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                        base_model=base_model, k=num_layers).to(device)
    model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    # optimizer = copy.deepcopy(optimizer_origin)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay)

    start = t()
    prev = start
    for epoch in range(1, num_epochs + 1):
        # loss = train_(model, data.x, data.edge_index)
        loss = train_1(model, optimizer, noisy_data.x, noisy_data.edge_index)

        now = t()
        if(epoch%10 == 0):
            print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
                    f'this epoch {now - prev:.4f}, total {now - start:.4f}')
        prev = now

    # print("=== Test ===")
    f1mi,f1ma = test(model, noisy_data.x, noisy_data.edge_index, noisy_data.y, idx_train, idx_clean_test, final=True)
    final_cl_acc_noisy.append(f1mi)
    gnn_model = model_construct(args,'GCN',noisy_data,device)
    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    gnn_model.fit(noisy_data.x, noisy_data.edge_index, None, noisy_data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(noisy_data.x,noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_clean_test)
    final_gnn_acc_noisy.append(clean_acc)
    print("accuracy of clean model on clean test nodes: {:.4f}".format(clean_acc))

print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc),np.std(final_cl_acc),np.average(final_gnn_acc),np.std(final_gnn_acc)))

print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc_noisy),np.std(final_cl_acc_noisy),np.average(final_gnn_acc_noisy),np.std(final_gnn_acc_noisy)))
# print("=== Nosi graph: random noise ===")
# for seed in seeds:
#     np.random.seed(seed)
#     # torch.manual_seed(seed)
#     # torch.cuda.manual_seed(seed)
#     encoder = Encoder(dataset.num_features, num_hidden, activation,
#                         base_model=base_model, k=num_layers).to(device)
#     model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
#     optimizer = torch.optim.Adam(
#         model.parameters(), lr=learning_rate, weight_decay=weight_decay)

#     start = t()
#     prev = start
#     for epoch in range(1, num_epochs + 1):
#         # loss = train_(model, data.x, data.edge_index)
#         loss = train_1(model, noisy_data.x, noisy_data.edge_index)

#         now = t()
#         # if(epoch%10 == 0):
#     
#     #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
#         #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
#         prev = now

#     print("=== Test ===")
#     test(model, noisy_data.x, noisy_data.edge_index, noisy_data.y, idx_train, idx_clean_test, final=True)


# ### Inductive

# In[ ]:


'''Transductive'''
import copy 
seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()

encoder = Encoder(dataset.num_features, num_hidden, activation,
                        base_model=base_model, k=num_layers).to(device)
model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
optimizer = torch.optim.Adam(
    model.parameters(), lr=learning_rate, weight_decay=weight_decay)
from models.GCN import GCN
from models.construct import model_construct
gnn_model = model_construct(args,'GCN',data,device)

model_origin = copy.deepcopy(model)
encoder_origin = copy.deepcopy(encoder)
optimizer_origin = copy.deepcopy(optimizer)
gnn_model_origin = copy.deepcopy(gnn_model)

# seeds = [args.seed]
final_cl_acc = []
final_gnn_acc = []
print("=== Raw graph ===")
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    print("Learn node representations via contrastive learning")
    model = copy.deepcopy(model_origin)
    encoder = copy.deepcopy(encoder_origin)
    # optimizer = copy.deepcopy(optimizer_origin)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    start = t()
    prev = start
    for epoch in range(1, num_epochs + 1):
        # loss = train_(model, data.x, data.edge_index)
        loss = train_1(model, optimizer, data.x, train_edge_index, None, seen_node_idx)

        now = t()
        # if(epoch%10 == 0):
        #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
        #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
        prev = now

    # print("=== Test ===")
    f1mi,f1ma = test(model, data.x, data.edge_index, data.y, idx_train, idx_clean_test, final=True)
    final_cl_acc.append(f1mi)
    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    gnn_model = copy.deepcopy(gnn_model_origin)
    gnn_model.fit(data.x, train_edge_index, None, data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(data.x,data.edge_index,data.edge_weight,data.y,idx_clean_test)
    final_gnn_acc.append(clean_acc)
    print("accuracy of clean model on clean test nodes: {:.4f}".format(clean_acc))
print("=== Noisy graph ===")
noisy_train_edge_index,_, edge_mask = subgraph(torch.bitwise_not(noisy_data.test_mask),noisy_data.edge_index,relabel_nodes=False)
final_cl_acc_noisy = []
final_gnn_acc_noisy = []
for seed in seeds:
    print("Learn node representations via contrastive learning")
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                        base_model=base_model, k=num_layers).to(device)
    model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    # optimizer = copy.deepcopy(optimizer_origin)
    optimizer = torch.optim.Adam(
        model.parameters(), lr=learning_rate, weight_decay=weight_decay)

    start = t()
    prev = start
    for epoch in range(1, num_epochs + 1):
        # loss = train_(model, data.x, data.edge_index)
        loss = train_1(model, optimizer, noisy_data.x, noisy_train_edge_index, None, seen_node_idx)

        now = t()
        # if(epoch%10 == 0):
        #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
        #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
        prev = now

    # print("=== Test ===")
    f1mi,f1ma = test(model, noisy_data.x, noisy_data.edge_index, noisy_data.y, idx_train, idx_clean_test, final=True)
    final_cl_acc_noisy.append(f1mi)
    gnn_model = model_construct(args,'GCN',noisy_data,device)
    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    gnn_model.fit(noisy_data.x, noisy_train_edge_index, None, noisy_data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(noisy_data.x,noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_clean_test)
    final_gnn_acc_noisy.append(clean_acc)
    print("accuracy of clean model on clean test nodes: {:.4f}".format(clean_acc))

print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc),np.std(final_cl_acc),np.average(final_gnn_acc),np.std(final_gnn_acc)))

print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc_noisy),np.std(final_cl_acc_noisy),np.average(final_gnn_acc_noisy),np.std(final_gnn_acc_noisy)))
# print("=== Nosi graph: random noise ===")
# for seed in seeds:
#     np.random.seed(seed)
#     # torch.manual_seed(seed)
#     # torch.cuda.manual_seed(seed)
#     encoder = Encoder(dataset.num_features, num_hidden, activation,
#                         base_model=base_model, k=num_layers).to(device)
#     model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
#     optimizer = torch.optim.Adam(
#         model.parameters(), lr=learning_rate, weight_decay=weight_decay)

#     start = t()
#     prev = start
#     for epoch in range(1, num_epochs + 1):
#         # loss = train_(model, data.x, data.edge_index)
#         loss = train_1(model, noisy_data.x, noisy_data.edge_index)

#         now = t()
#         # if(epoch%10 == 0):
#     
#     #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
#         #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
#         prev = now

#     print("=== Test ===")
#     test(model, noisy_data.x, noisy_data.edge_index, noisy_data.y, idx_train, idx_clean_test, final=True)


# ## Backdoor Attack

# ### Contrastive 

# In[ ]:


''' Contrastive learning to backdoor in Contrastive learning'''
from models.GTA import Backdoor
import heuristic_selection as hs
from models.GCN import GCN
from models.construct import model_construct

size = args.vs_number #int((len(data.test_mask)-data.test_mask.sum())*args.vs_ratio)
print("#Attach Nodes:{}".format(size))
from models.construct import model_construct
result_asr = []
result_acc = []
data = data.to(device)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.1,mode='random_noise')
noisy_data = noisy_data.to(device)
seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
# seeds = [args.seed]
for seed in seeds:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    args.seed = seed
    if(args.selection_method == 'none'):
        idx_attach = hs.obtain_attach_nodes(args,unlabeled_idx,size)
    elif(args.selection_method == 'cluster'):
        idx_attach = hs.cluster_distance_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    elif(args.selection_method == 'cluster_degree'):
        idx_attach = hs.cluster_degree_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    # train trigger generator 
    model = Backdoor(args,device)
    model.fit(data.x, train_edge_index, None, data.y, idx_train,idx_attach, unlabeled_idx)
    poison_x, poison_edge_index, poison_edge_weights, poison_labels = model.get_poisoned()
    bkd_tn_nodes = torch.cat([idx_train,idx_attach]).to(device)
    # learn contrastive node representation
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                        base_model=base_model, k=num_layers).to(device)
    contrastive_model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    optimizer = torch.optim.Adam(
        contrastive_model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    
    start = t()
    prev = start
    for epoch in range(1, num_epochs + 1):
        # loss = train_(model, data.x, data.edge_index)
        loss = train_1(contrastive_model, optimizer, poison_x, poison_edge_index, poison_edge_weights, seen_node_idx)

        now = t()
        # if(epoch%10 == 0):
        #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
        #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
        prev = now

    contrastive_model.eval()
    cont_poison_x = contrastive_model(poison_x, poison_edge_index, poison_edge_weights).detach().to(device)

    print("precent of left attach nodes: {:.3f}"        .format(len(set(bkd_tn_nodes.tolist()) & set(idx_attach.tolist()))/len(idx_attach)))
    #%%

    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    # test_model = model_construct(args,args.test_model,data,device).to(device) 
    test_model = GCN(nfeat=cont_poison_x.shape[1],                nhid=args.hidden,                nclass= int(data.y.max()+1),                dropout=args.dropout,                lr=args.train_lr,                weight_decay=args.weight_decay,                device=device).to(device) 
    test_model.fit(cont_poison_x, poison_edge_index, poison_edge_weights, poison_labels, bkd_tn_nodes, idx_val,train_iters=args.epochs,verbose=False)

    output = test_model(cont_poison_x,poison_edge_index,poison_edge_weights)
    train_attach_rate = (output.argmax(dim=1)[idx_attach]==args.target_class).float().mean()
    print("target class rate on Vs: {:.4f}".format(train_attach_rate))
    torch.cuda.empty_cache()
    #%%
    induct_edge_index = torch.cat([poison_edge_index,mask_edge_index],dim=1)
    induct_edge_weights = torch.cat([poison_edge_weights,torch.ones([mask_edge_index.shape[1]],dtype=torch.float,device=device)])
    clean_acc = test_model.test(cont_poison_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
    # test_model = test_model.cpu()

    print("accuracy on clean test nodes: {:.4f}".format(clean_acc))


    if(args.evaluate_mode == '1by1'):
        from torch_geometric.utils  import k_hop_subgraph
        overall_induct_edge_index, overall_induct_edge_weights = induct_edge_index.clone(),induct_edge_weights.clone()
        asr = 0
        flip_asr = 0
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        for i, idx in enumerate(idx_atk):
            idx=int(idx)
            sub_induct_nodeset, sub_induct_edge_index, sub_mapping, sub_edge_mask  = k_hop_subgraph(node_idx = [idx], num_hops = 2, edge_index = overall_induct_edge_index, relabel_nodes=True) # sub_mapping means the index of [idx] in sub)nodeset
            ori_node_idx = sub_induct_nodeset[sub_mapping]
            relabeled_node_idx = sub_mapping
            sub_induct_edge_weights = torch.ones([sub_induct_edge_index.shape[1]]).to(device)
            # inject trigger on attack test nodes (idx_atk)'''
            with torch.no_grad():
                induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(relabeled_node_idx,poison_x[sub_induct_nodeset],sub_induct_edge_index,sub_induct_edge_weights,device)
                induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
                cont_induct_x = contrastive_model(induct_x, induct_edge_index,induct_edge_weights).detach().to(device)
                # # do pruning in test datas'''
                # if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
                #     induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device,large_graph=False)
                # attack evaluation

                output = test_model(cont_induct_x,induct_edge_index,induct_edge_weights)
                train_attach_rate = (output.argmax(dim=1)[relabeled_node_idx]==args.target_class).float().mean()
                # print("Node {}: {}, Origin Label: {}".format(i, idx, data.y[idx]))
                # print("ASR: {:.4f}".format(train_attach_rate))
                asr += train_attach_rate
                if(data.y[idx] != args.target_class):
                    flip_asr += train_attach_rate
        asr = asr/(idx_atk.shape[0])
        flip_asr = flip_asr/(flip_idx_atk.shape[0])
        print("Overall ASR: {:.4f}".format(asr))
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
    elif(args.evaluate_mode == 'overall'):
        # %% inject trigger on attack test nodes (idx_atk)'''
        induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(idx_atk,poison_x,induct_edge_index,induct_edge_weights,device)
        induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
        # do pruning in test datas'''
        if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
            induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device)
        # attack evaluation

        # test_model = test_model.to(device)
        output = test_model(induct_x,induct_edge_index,induct_edge_weights)
        train_attach_rate = (output.argmax(dim=1)[idx_atk]==args.target_class).float().mean()
        print("ASR: {:.4f}".format(train_attach_rate))
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        flip_asr = (output.argmax(dim=1)[flip_idx_atk]==args.target_class).float().mean()
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
        ca = test_model.test(induct_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
        print("CA: {:.4f}".format(ca))

    result_asr.append(float(asr))
    result_acc.append(float(clean_acc))

print('The final ASR:{:.5f}, {:.5f}, Accuracy:{:.5f}, {:.5f}'            .format(np.average(result_asr),np.std(result_asr),np.average(result_acc),np.std(result_acc)))


# ### GNN Classifier

# In[ ]:


'''Backdoor attack to GNN classifier'''
from models.GTA import Backdoor
import heuristic_selection as hs
from models.GCN import GCN
from models.construct import model_construct

size = args.vs_number #int((len(data.test_mask)-data.test_mask.sum())*args.vs_ratio)
print("#Attach Nodes:{}".format(size))
from models.construct import model_construct
result_asr = []
result_acc = []
data = data.to(device)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.1,mode='random_noise')
data = noisy_data.to(device)
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
# seeds = [args.seed]
for seed in seeds:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    args.seed = seed
    if(args.selection_method == 'none'):
        idx_attach = hs.obtain_attach_nodes(args,unlabeled_idx,size)
    elif(args.selection_method == 'cluster'):
        idx_attach = hs.cluster_distance_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    elif(args.selection_method == 'cluster_degree'):
        idx_attach = hs.cluster_degree_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    # train trigger generator 
    model = Backdoor(args,device)
    model.fit(data.x, train_edge_index, None, data.y, idx_train,idx_attach, unlabeled_idx)
    poison_x, poison_edge_index, poison_edge_weights, poison_labels = model.get_poisoned()
    bkd_tn_nodes = torch.cat([idx_train,idx_attach]).to(device)
    # # learn contrastive node representation
    # encoder = Encoder(dataset.num_features, num_hidden, activation,
    #                     base_model=base_model, k=num_layers).to(device)
    # contrastive_model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    # optimizer = torch.optim.Adam(
    #     contrastive_model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    
    # start = t()
    # prev = start
    # for epoch in range(1, num_epochs + 1):
    #     # loss = train_(model, data.x, data.edge_index)
    #     loss = train_1(contrastive_model, poison_x, poison_edge_index, poison_edge_weights)

    #     now = t()
    #     if(epoch%10 == 0):
    #         print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
    #                 f'this epoch {now - prev:.4f}, total {now - start:.4f}')
    #     prev = now

    # contrastive_model.eval()
    # cont_poison_x = contrastive_model(poison_x, poison_edge_index, poison_edge_weights).detach().to(device)

    print("precent of left attach nodes: {:.3f}"        .format(len(set(bkd_tn_nodes.tolist()) & set(idx_attach.tolist()))/len(idx_attach)))
    #%%

    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    test_model = model_construct(args,args.test_model,data,device).to(device) 
    # test_model = GCN(nfeat=cont_poison_x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device).to(device) 
    test_model.fit(poison_x, poison_edge_index, poison_edge_weights, poison_labels, bkd_tn_nodes, idx_val,train_iters=args.epochs,verbose=False)

    output = test_model(poison_x,poison_edge_index,poison_edge_weights)
    train_attach_rate = (output.argmax(dim=1)[idx_attach]==args.target_class).float().mean()
    print("target class rate on Vs: {:.4f}".format(train_attach_rate))
    torch.cuda.empty_cache()
    #%%
    induct_edge_index = torch.cat([poison_edge_index,mask_edge_index],dim=1)
    induct_edge_weights = torch.cat([poison_edge_weights,torch.ones([mask_edge_index.shape[1]],dtype=torch.float,device=device)])
    clean_acc = test_model.test(poison_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
    # test_model = test_model.cpu()

    print("accuracy on clean test nodes: {:.4f}".format(clean_acc))


    if(args.evaluate_mode == '1by1'):
        from torch_geometric.utils  import k_hop_subgraph
        overall_induct_edge_index, overall_induct_edge_weights = induct_edge_index.clone(),induct_edge_weights.clone()
        asr = 0
        flip_asr = 0
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        for i, idx in enumerate(idx_atk):
            idx=int(idx)
            sub_induct_nodeset, sub_induct_edge_index, sub_mapping, sub_edge_mask  = k_hop_subgraph(node_idx = [idx], num_hops = 2, edge_index = overall_induct_edge_index, relabel_nodes=True) # sub_mapping means the index of [idx] in sub)nodeset
            ori_node_idx = sub_induct_nodeset[sub_mapping]
            relabeled_node_idx = sub_mapping
            sub_induct_edge_weights = torch.ones([sub_induct_edge_index.shape[1]]).to(device)
            # inject trigger on attack test nodes (idx_atk)'''
            with torch.no_grad():
                induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(relabeled_node_idx,poison_x[sub_induct_nodeset],sub_induct_edge_index,sub_induct_edge_weights,device)
                induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
                # cont_induct_x = contrastive_model(induct_x, induct_edge_index,induct_edge_weights).detach().to(device)
                # # do pruning in test datas'''
                # if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
                #     induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device,large_graph=False)
                # attack evaluation

                output = test_model(induct_x,induct_edge_index,induct_edge_weights)
                train_attach_rate = (output.argmax(dim=1)[relabeled_node_idx]==args.target_class).float().mean()
                # print("Node {}: {}, Origin Label: {}".format(i, idx, data.y[idx]))
                # print("ASR: {:.4f}".format(train_attach_rate))
                asr += train_attach_rate
                if(data.y[idx] != args.target_class):
                    flip_asr += train_attach_rate
        asr = asr/(idx_atk.shape[0])
        flip_asr = flip_asr/(flip_idx_atk.shape[0])
        print("Overall ASR: {:.4f}".format(asr))
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
    result_asr.append(float(asr))
    result_acc.append(float(clean_acc))
print('The final ASR:{:.5f}, {:.5f}, Accuracy:{:.5f}, {:.5f}'            .format(np.average(result_asr),np.std(result_asr),np.average(result_acc),np.std(result_acc)))


# # Unify Contrastive GNN

# ## Structure noise

# ### Noisy

# In[8]:


import copy 
from model import UnifyModel
from models.construct import model_construct
data = data.to(device)
config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]
num_epochs = config['num_epochs']
learning_rate = config['learning_rate']
weight_decay = config['weight_decay']
args.seed = config['seed']
args.cont_batch_size = config['cont_batch_size']
args.cont_weight = config['cont_weight']
args.add_edge_rate_1 = config['add_edge_rate_1']
args.add_edge_rate_2 = config['add_edge_rate_2']
args.drop_edge_rate_1 = config['drop_edge_rate_1']
args.drop_edge_rate_2 = config['drop_edge_rate_2']
args.drop_feat_rate_1 = config['drop_feature_rate_1']
args.drop_feat_rate_2 = config['drop_feature_rate_2']
# args.add_edge_rate_1 = 0
# args.add_edge_rate_2 = 0
# args.drop_edge_rate_1 = 0.3
# args.drop_edge_rate_2 = 0.5
# args.drop_feat_rate_1 = 0.4
# args.drop_feat_rate_2 = 0.4
num_class = int(data.y.max()+1)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.10,mode='random_noise')
noisy_data = noisy_data.to(device)

# diff_dataset = PPRDataset(noisy_data,args.dataset)
# diff_noisy_data = diff_dataset.data.to(device)

seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()
idx_overall_test = (torch.bitwise_not(data.train_mask)&torch.bitwise_not(data.val_mask)).nonzero().flatten()



final_cl_acc_noisy = []
final_gnn_acc_noisy = []
print("=== Noisy graph ===")
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    '''Transductive'''
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                            base_model=base_model, k=num_layers).to(device)
    model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=device).to(device)
    # model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=device,data1=noisy_data,data2=diff_noisy_data).to(device)
    model.fit(args, noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_train,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=None)
    # model.fit_1(args, noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_train,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=None)
    # x, edge_index,edge_weight,labels,idx_train,idx_val=None,cont_iters=None,train_iters=200,seen_node_idx = None
    acc_cl = model.test(noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_clean_test)
    print("Accuracy of GNN+CL: {}".format(acc_cl))
    final_cl_acc_noisy.append(acc_cl)
    gnn_model = model_construct(args,'GCN',noisy_data,device)
    gnn_model.fit(noisy_data.x, noisy_data.edge_index, None, noisy_data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(noisy_data.x,noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_overall_test)
    print(clean_acc)
    final_gnn_acc_noisy.append(clean_acc)


print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc_noisy),np.std(final_cl_acc_noisy),np.average(final_gnn_acc_noisy),np.std(final_gnn_acc_noisy)))


# ### Raw

# In[86]:


import copy 
from model import UnifyModel
from models.construct import model_construct
data = data.to(device)
config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]
num_epochs = config['num_epochs']
learning_rate = config['learning_rate']
weight_decay = config['weight_decay']
args.seed = config['seed']
args.cont_batch_size = config['cont_batch_size']
args.cont_weight = config['cont_weight']
args.add_edge_rate_1 = config['add_edge_rate_1']
args.add_edge_rate_2 = config['add_edge_rate_2']
args.drop_edge_rate_1 = config['drop_edge_rate_1']
args.drop_edge_rate_2 = config['drop_edge_rate_2']
args.drop_feat_rate_1 = config['drop_feature_rate_1']
args.drop_feat_rate_2 = config['drop_feature_rate_2']
# args.add_edge_rate_1 = 0
# args.add_edge_rate_2 = 0
# args.drop_edge_rate_1 = 0.3
# args.drop_edge_rate_2 = 0.5
# args.drop_feat_rate_1 = 0.4
# args.drop_feat_rate_2 = 0.4
num_class = int(data.y.max()+1)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.25,mode='random_noise')
noisy_data = noisy_data.to(device)
seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()
idx_overall_test = (torch.bitwise_not(data.train_mask)&torch.bitwise_not(data.val_mask)).nonzero().flatten()


final_cl_acc = []
final_gnn_acc = []
print("=== Raw graph ===")
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    '''Transductive'''
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                            base_model=base_model, k=num_layers).to(device)
    model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=None).to(device)
    model.fit(args, data.x, data.edge_index,data.edge_weight,data.y,idx_train,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=None)
    # x, edge_index,edge_weight,labels,idx_train,idx_val=None,cont_iters=None,train_iters=200,seen_node_idx = None
    acc_cl = model.test(data.x, data.edge_index,data.edge_weight,data.y,idx_clean_test)
    print("Accuracy of GNN+CL: {}".format(acc_cl))
    final_cl_acc.append(acc_cl)
    gnn_model = model_construct(args,'GCN',data,device)
    gnn_model.fit(data.x, data.edge_index, None, data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(data.x,data.edge_index,data.edge_weight,data.y,idx_overall_test)
    print(clean_acc)
    final_gnn_acc.append(clean_acc)


print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc),np.std(final_cl_acc),np.average(final_gnn_acc),np.std(final_gnn_acc)))


# ### Noisy+DIffuse

# In[91]:


import copy 
from model import UnifyModel
from models.construct import model_construct
data = data.to(device)
config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]
num_epochs = config['num_epochs']
learning_rate = config['learning_rate']
weight_decay = config['weight_decay']
args.seed = config['seed']
args.cont_batch_size = config['cont_batch_size']
args.cont_weight = config['cont_weight']
args.add_edge_rate_1 = config['add_edge_rate_1']
args.add_edge_rate_2 = config['add_edge_rate_2']
args.drop_edge_rate_1 = config['drop_edge_rate_1']
args.drop_edge_rate_2 = config['drop_edge_rate_2']
args.drop_feat_rate_1 = config['drop_feature_rate_1']
args.drop_feat_rate_2 = config['drop_feature_rate_2']
# args.add_edge_rate_1 = 0
# args.add_edge_rate_2 = 0
# args.drop_edge_rate_1 = 0.3
# args.drop_edge_rate_2 = 0.5
# args.drop_feat_rate_1 = 0.4
# args.drop_feat_rate_2 = 0.4
num_class = int(data.y.max()+1)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.25,mode='random_noise')
noisy_data = noisy_data.to(device)

diff_dataset = PPRDataset(noisy_data,args.dataset)
diff_noisy_data = diff_dataset.data.to(device)

seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()
idx_overall_test = (torch.bitwise_not(data.train_mask)&torch.bitwise_not(data.val_mask)).nonzero().flatten()



final_cl_acc_noisy = []
final_gnn_acc_noisy = []
print("=== Noisy graph ===")
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    '''Transductive'''
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                            base_model=base_model, k=num_layers).to(device)
    # model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=device).to(device)
    model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=device,data1=noisy_data,data2=diff_noisy_data).to(device)
    # model.fit(args, noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_train,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=None)
    model.fit_1(args, noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_train,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=None)
    # x, edge_index,edge_weight,labels,idx_train,idx_val=None,cont_iters=None,train_iters=200,seen_node_idx = None
    acc_cl = model.test(noisy_data.x, noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_clean_test)
    print("Accuracy of GNN+CL: {}".format(acc_cl))
    final_cl_acc_noisy.append(acc_cl)
    gnn_model = model_construct(args,'GCN',noisy_data,device)
    gnn_model.fit(noisy_data.x, noisy_data.edge_index, None, noisy_data.y, idx_train, idx_val,train_iters=args.epochs,verbose=False)
    clean_acc = gnn_model.test(noisy_data.x,noisy_data.edge_index,noisy_data.edge_weight,noisy_data.y,idx_overall_test)
    print(clean_acc)
    final_gnn_acc_noisy.append(clean_acc)


print('The final CL Acc:{:.5f}, {:.5f}, The final GNN Acc:{:.5f}, {:.5f}'            .format(np.average(final_cl_acc_noisy),np.std(final_cl_acc_noisy),np.average(final_gnn_acc_noisy),np.std(final_gnn_acc_noisy)))


# ## Backdoor

# #### GNN+CL

# In[77]:


''' Contrastive learning to backdoor in Contrastive learning'''
from models.GTA import Backdoor
import heuristic_selection as hs
from models.GCN import GCN
from models.construct import model_construct

import copy 
from model import UnifyModel
from models.construct import model_construct
config = yaml.load(open(args.config), Loader=SafeLoader)[args.dataset]
args.homo_loss_weight=config['homo_loss_weight']
args.homo_boost_thrd = config['homo_boost_thrd']
args.trojan_epochs = config['trojan_epochs']
args.selection_method = config['selection_method']
args.vs_number=config['vs_number']
args.num_epochs = config['num_epochs']
args.seed = config['seed']
args.cont_batch_size = config['cont_batch_size']
args.cont_weight = config['cont_weight']
args.add_edge_rate_1 = config['add_edge_rate_1']
args.add_edge_rate_2 = config['add_edge_rate_2']
args.drop_edge_rate_1 = config['drop_edge_rate_1']
args.drop_edge_rate_2 = config['drop_edge_rate_2']
args.drop_feat_rate_1 = config['drop_feature_rate_1']
args.drop_feat_rate_2 = config['drop_feature_rate_2']
# args.add_edge_rate_1 = 0
# args.add_edge_rate_2 = 0
# args.drop_edge_rate_1 = 0.3
# args.drop_edge_rate_2 = 0.5
# args.drop_feat_rate_1 = 0.4
# args.drop_feat_rate_2 = 0.4
data = data.to(device)
# learning_rate = 0.0002
weight_decay = config['weight_decay']
num_class = int(data.y.max()+1)


size = args.vs_number #int((len(data.test_mask)-data.test_mask.sum())*args.vs_ratio)
print("#Attach Nodes:{}".format(size))
from models.construct import model_construct
result_asr = []
result_acc = []
data = data.to(device)

seen_node_idx = (torch.bitwise_not(data.test_mask)).nonzero().flatten()
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
# seeds = [args.seed]
for seed in seeds:
    np.random.seed(seed)
    # torch.manual_seed(seed)
    # torch.cuda.manual_seed(seed)
    args.seed = seed
    if(args.selection_method == 'none'):
        idx_attach = hs.obtain_attach_nodes(args,unlabeled_idx,size)
    elif(args.selection_method == 'cluster'):
        idx_attach = hs.cluster_distance_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    elif(args.selection_method == 'cluster_degree'):
        idx_attach = hs.cluster_degree_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    # train trigger generator 
    model = Backdoor(args,device)
    model.fit(data.x, train_edge_index, None, data.y, idx_train,idx_attach, unlabeled_idx)
    poison_x, poison_edge_index, poison_edge_weights, poison_labels = model.get_poisoned()
    bkd_tn_nodes = torch.cat([idx_train,idx_attach]).to(device)
    # learn contrastive node representation
    # encoder = Encoder(dataset.num_features, num_hidden, activation,
    #                         base_model=base_model, k=num_layers).to(device)
    # contrastive_model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=None).to(device)
    # contrastive_model.fit(args, poison_x, poison_edge_index,poison_edge_weights,poison_labels,idx_train,idx_val=idx_val,train_iters=1000,cont_iters=num_epochs,seen_node_idx=seen_node_idx)
    # encoder = Encoder(dataset.num_features, num_hidden, activation,
    #                     base_model=base_model, k=num_layers).to(device)
    # contrastive_model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    # optimizer = torch.optim.Adam(
    #     contrastive_model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    
    # start = t()
    # prev = start
    # for epoch in range(1, num_epochs + 1):
    #     # loss = train_(model, data.x, data.edge_index)
    #     loss = train_1(contrastive_model, optimizer, poison_x, poison_edge_index, poison_edge_weights, seen_node_idx)

    #     now = t()
    #     # if(epoch%10 == 0):
    #     #     print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
    #     #             f'this epoch {now - prev:.4f}, total {now - start:.4f}')
    #     prev = now

    # contrastive_model.eval()
    # cont_poison_x = contrastive_model(poison_x, poison_edge_index, poison_edge_weights).detach().to(device)

    print("precent of left attach nodes: {:.3f}"        .format(len(set(bkd_tn_nodes.tolist()) & set(idx_attach.tolist()))/len(idx_attach)))
    #%%

    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    # test_model = model_construct(args,args.test_model,data,device).to(device) 
    encoder = Encoder(dataset.num_features, num_hidden, activation,
                            base_model=base_model, k=num_layers).to(device)
    test_model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=None).to(device)
    # test_model = UnifyModel(args, encoder, num_hidden, num_proj_hidden, num_class, tau, lr=learning_rate, weight_decay=weight_decay, device=device,data1=noisy_data,data2=diff_noisy_data).to(device)
    test_model.fit(args, poison_x, poison_edge_index,poison_edge_weights,poison_labels,bkd_tn_nodes,idx_val=idx_val,train_iters=num_epochs,cont_iters=num_epochs,seen_node_idx=seen_node_idx)
    test_embds = test_model(poison_x,poison_edge_index,poison_edge_weights)
    output = test_model.clf_head(test_embds)
    train_attach_rate = (output.argmax(dim=1)[idx_attach]==args.target_class).float().mean()
    print("target class rate on Vs: {:.4f}".format(train_attach_rate))
    torch.cuda.empty_cache()
    # test_model = GCN(nfeat=cont_poison_x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device).to(device) 
    # test_model.fit(cont_poison_x, poison_edge_index, poison_edge_weights, poison_labels, bkd_tn_nodes, idx_val,train_iters=args.epochs,verbose=False)

    # output = test_model(cont_poison_x,poison_edge_index,poison_edge_weights)
    # train_attach_rate = (output.argmax(dim=1)[idx_attach]==args.target_class).float().mean()
    # print("target class rate on Vs: {:.4f}".format(train_attach_rate))
    # torch.cuda.empty_cache()
    #%%
    induct_edge_index = torch.cat([poison_edge_index,mask_edge_index],dim=1)
    induct_edge_weights = torch.cat([poison_edge_weights,torch.ones([mask_edge_index.shape[1]],dtype=torch.float,device=device)])
    clean_acc = test_model.test(poison_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
    # test_model = test_model.cpu()

    print("accuracy on clean test nodes: {:.4f}".format(clean_acc))


    if(args.evaluate_mode == '1by1'):
        from torch_geometric.utils  import k_hop_subgraph
        overall_induct_edge_index, overall_induct_edge_weights = induct_edge_index.clone(),induct_edge_weights.clone()
        asr = 0
        flip_asr = 0
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        for i, idx in enumerate(idx_atk):
            idx=int(idx)
            sub_induct_nodeset, sub_induct_edge_index, sub_mapping, sub_edge_mask  = k_hop_subgraph(node_idx = [idx], num_hops = 2, edge_index = overall_induct_edge_index, relabel_nodes=True) # sub_mapping means the index of [idx] in sub)nodeset
            ori_node_idx = sub_induct_nodeset[sub_mapping]
            relabeled_node_idx = sub_mapping
            sub_induct_edge_weights = torch.ones([sub_induct_edge_index.shape[1]]).to(device)
            # inject trigger on attack test nodes (idx_atk)'''
            with torch.no_grad():
                induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(relabeled_node_idx,poison_x[sub_induct_nodeset],sub_induct_edge_index,sub_induct_edge_weights,device)
                induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
                test_embeds = test_model(induct_x, induct_edge_index,induct_edge_weights).to(device)
                output = test_model.clf_head(test_embeds)
                # cont_induct_x = contrastive_model(induct_x, induct_edge_index,induct_edge_weights).detach().to(device)
                # # do pruning in test datas'''
                # if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
                #     induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device,large_graph=False)
                # attack evaluation

                # output = test_model(cont_induct_x,induct_edge_index,induct_edge_weights)
                train_attach_rate = (output.argmax(dim=1)[relabeled_node_idx]==args.target_class).float().mean()
                # print("Node {}: {}, Origin Label: {}".format(i, idx, data.y[idx]))
                # print("ASR: {:.4f}".format(train_attach_rate))
                asr += train_attach_rate
                if(data.y[idx] != args.target_class):
                    flip_asr += train_attach_rate
        asr = asr/(idx_atk.shape[0])
        flip_asr = flip_asr/(flip_idx_atk.shape[0])
        print("Overall ASR: {:.4f}".format(asr))
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
    elif(args.evaluate_mode == 'overall'):
        # %% inject trigger on attack test nodes (idx_atk)'''
        induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(idx_atk,poison_x,induct_edge_index,induct_edge_weights,device)
        induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
        # do pruning in test datas'''
        if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
            induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device)
        # attack evaluation

        test_embeds = test_model(induct_x, induct_edge_index,induct_edge_weights).to(device)
        output = test_model.clf_head(test_embeds)
        # test_model = test_model.to(device)
        # output = test_model(induct_x,induct_edge_index,induct_edge_weights)
        train_attach_rate = (output.argmax(dim=1)[idx_atk]==args.target_class).float().mean()
        print("ASR: {:.4f}".format(train_attach_rate))
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        flip_asr = (output.argmax(dim=1)[flip_idx_atk]==args.target_class).float().mean()
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
        ca = test_model.test(induct_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
        print("CA: {:.4f}".format(ca))

    result_asr.append(float(asr))
    result_acc.append(float(clean_acc))

print('The final ASR:{:.5f}, {:.5f}, Accuracy:{:.5f}, {:.5f}'            .format(np.average(result_asr),np.std(result_asr),np.average(result_acc),np.std(result_acc)))


# In[45]:


num_epochs


# ### GNN

# In[40]:


'''Backdoor attack to GNN classifier'''
from models.GTA import Backdoor
import heuristic_selection as hs
from models.GCN import GCN
from models.construct import model_construct
args.homo_loss_weight=config['homo_loss_weight']
args.vs_number=config['vs_number']
args.trojan_epochs = config['trojan_epochs']

data = data.to(device)
num_class = int(data.y.max()+1)
args.seed = config['seed']

size = args.vs_number #int((len(data.test_mask)-data.test_mask.sum())*args.vs_ratio)
print("#Attach Nodes:{}".format(size))
from models.construct import model_construct
result_asr = []
result_acc = []
data = data.to(device)

noisy_data = construct_noisy_graph(data,perturb_ratio=0.1,mode='random_noise')
data = noisy_data.to(device)
rs = np.random.RandomState(args.seed)
seeds = rs.randint(1000,size=3)
# seeds = [args.seed]
for seed in seeds:
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    args.seed = seed
    if(args.selection_method == 'none'):
        idx_attach = hs.obtain_attach_nodes(args,unlabeled_idx,size)
    elif(args.selection_method == 'cluster'):
        idx_attach = hs.cluster_distance_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    elif(args.selection_method == 'cluster_degree'):
        idx_attach = hs.cluster_degree_selection(args,data,idx_train,idx_val,idx_clean_test,unlabeled_idx,train_edge_index,size,device)
        idx_attach = torch.LongTensor(idx_attach).to(device)
    # train trigger generator 
    model = Backdoor(args,device)
    model.fit(data.x, train_edge_index, None, data.y, idx_train,idx_attach, unlabeled_idx)
    poison_x, poison_edge_index, poison_edge_weights, poison_labels = model.get_poisoned()
    bkd_tn_nodes = torch.cat([idx_train,idx_attach]).to(device)
    # # learn contrastive node representation
    # encoder = Encoder(dataset.num_features, num_hidden, activation,
    #                     base_model=base_model, k=num_layers).to(device)
    # contrastive_model = Model(encoder, num_hidden, num_proj_hidden, tau).to(device)
    # optimizer = torch.optim.Adam(
    #     contrastive_model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    
    # start = t()
    # prev = start
    # for epoch in range(1, num_epochs + 1):
    #     # loss = train_(model, data.x, data.edge_index)
    #     loss = train_1(contrastive_model, poison_x, poison_edge_index, poison_edge_weights)

    #     now = t()
    #     if(epoch%10 == 0):
    #         print(f'(T) | Epoch={epoch:03d}, loss={loss:.4f}, '
    #                 f'this epoch {now - prev:.4f}, total {now - start:.4f}')
    #     prev = now

    # contrastive_model.eval()
    # cont_poison_x = contrastive_model(poison_x, poison_edge_index, poison_edge_weights).detach().to(device)

    print("precent of left attach nodes: {:.3f}"        .format(len(set(bkd_tn_nodes.tolist()) & set(idx_attach.tolist()))/len(idx_attach)))
    #%%

    # model = GCN(nfeat=data.x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device)
    test_model = model_construct(args,args.test_model,data,device).to(device) 
    # test_model = GCN(nfeat=cont_poison_x.shape[1],\
    #             nhid=args.hidden,\
    #             nclass= int(data.y.max()+1),\
    #             dropout=args.dropout,\
    #             lr=args.train_lr,\
    #             weight_decay=args.weight_decay,\
    #             device=device).to(device) 
    test_model.fit(poison_x, poison_edge_index, poison_edge_weights, poison_labels, bkd_tn_nodes, idx_val,train_iters=args.epochs,verbose=False)

    output = test_model(poison_x,poison_edge_index,poison_edge_weights)
    train_attach_rate = (output.argmax(dim=1)[idx_attach]==args.target_class).float().mean()
    print("target class rate on Vs: {:.4f}".format(train_attach_rate))
    torch.cuda.empty_cache()
    #%%
    induct_edge_index = torch.cat([poison_edge_index,mask_edge_index],dim=1)
    induct_edge_weights = torch.cat([poison_edge_weights,torch.ones([mask_edge_index.shape[1]],dtype=torch.float,device=device)])
    clean_acc = test_model.test(poison_x,induct_edge_index,induct_edge_weights,data.y,idx_clean_test)
    # test_model = test_model.cpu()

    print("accuracy on clean test nodes: {:.4f}".format(clean_acc))


    if(args.evaluate_mode == '1by1'):
        from torch_geometric.utils  import k_hop_subgraph
        overall_induct_edge_index, overall_induct_edge_weights = induct_edge_index.clone(),induct_edge_weights.clone()
        asr = 0
        flip_asr = 0
        flip_idx_atk = idx_atk[(data.y[idx_atk] != args.target_class).nonzero().flatten()]
        for i, idx in enumerate(idx_atk):
            idx=int(idx)
            sub_induct_nodeset, sub_induct_edge_index, sub_mapping, sub_edge_mask  = k_hop_subgraph(node_idx = [idx], num_hops = 2, edge_index = overall_induct_edge_index, relabel_nodes=True) # sub_mapping means the index of [idx] in sub)nodeset
            ori_node_idx = sub_induct_nodeset[sub_mapping]
            relabeled_node_idx = sub_mapping
            sub_induct_edge_weights = torch.ones([sub_induct_edge_index.shape[1]]).to(device)
            # inject trigger on attack test nodes (idx_atk)'''
            with torch.no_grad():
                induct_x, induct_edge_index,induct_edge_weights = model.inject_trigger(relabeled_node_idx,poison_x[sub_induct_nodeset],sub_induct_edge_index,sub_induct_edge_weights,device)
                induct_x, induct_edge_index,induct_edge_weights = induct_x.clone().detach(), induct_edge_index.clone().detach(),induct_edge_weights.clone().detach()
                # cont_induct_x = contrastive_model(induct_x, induct_edge_index,induct_edge_weights).detach().to(device)
                # # do pruning in test datas'''
                # if(args.defense_mode == 'prune' or args.defense_mode == 'isolate'):
                #     induct_edge_index,induct_edge_weights = prune_unrelated_edge(args,induct_edge_index,induct_edge_weights,induct_x,device,large_graph=False)
                # attack evaluation

                output = test_model(induct_x,induct_edge_index,induct_edge_weights)
                train_attach_rate = (output.argmax(dim=1)[relabeled_node_idx]==args.target_class).float().mean()
                # print("Node {}: {}, Origin Label: {}".format(i, idx, data.y[idx]))
                # print("ASR: {:.4f}".format(train_attach_rate))
                asr += train_attach_rate
                if(data.y[idx] != args.target_class):
                    flip_asr += train_attach_rate
        asr = asr/(idx_atk.shape[0])
        flip_asr = flip_asr/(flip_idx_atk.shape[0])
        print("Overall ASR: {:.4f}".format(asr))
        print("Flip ASR: {:.4f}/{} nodes".format(flip_asr,flip_idx_atk.shape[0]))
    result_asr.append(float(asr))
    result_acc.append(float(clean_acc))
print('The final ASR:{:.5f}, {:.5f}, Accuracy:{:.5f}, {:.5f}'            .format(np.average(result_asr),np.std(result_asr),np.average(result_acc),np.std(result_acc)))


# In[ ]:



