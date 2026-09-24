"""Chain metadata and RPC settings owned by the standalone rebalancer."""
import json
import os
from dotenv import load_dotenv
from web3 import Web3
from .paths import ENV_FILE

load_dotenv(ENV_FILE, override=False)

MASTERCHEF_V3_ADDRESSES = {
    "ETH": "0x556B9306565093C855AEA9AE92A594704c2Cd59e",
    "BNB": "0x556B9306565093C855AEA9AE92A594704c2Cd59e",
    "BAS": "0xC6A2Db661D5a5690172d8eB0a7DEA2d3008665A3",
    "LIN": "0x22E2f236065B780FA33EC8C4E58b99ebc8B55c57",
    "ARB": "0x5e09ACf80C0296740eC5d6F643005a4ef8DaA694",
    "MON": "0x5e09ACf80C0296740eC5d6F643005a4ef8DaA694",
    "POL": "0xe9c7f3196ab8c09f6616365e8873daeb207c0391",
    # "ERA": "0x4c615E78c5fCA1Ad31e4d66eb0D8688d84307463"
}

NPM_ADDRESSES = {
    "ETH": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "BNB": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "BAS": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "ARB": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "LIN": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "MON": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364",
    "POL": "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
}

AERODROME_FACTORY_NPM_ADDRESSES = {
    "BAS": {
        Web3.to_checksum_address("0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A"):
            Web3.to_checksum_address("0x827922686190790b37229fd06084350E74485b72"),
        Web3.to_checksum_address("0xaDe65c38CD4849aDBA595a4323a8C7DdfE89716a"):
            Web3.to_checksum_address("0xa990C6a764b73BF43cee5Bb40339c3322FB9D55F"),
        Web3.to_checksum_address("0xf8f2eB4940CFE7d13603DDDD87f123820Fc061Ef"):
            Web3.to_checksum_address("0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"),
    }
}

FACTORY_ADDRESSES = {
    'ETH': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'BNB': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'BAS': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'ARB': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'LIN': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'MON': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"),
    'POL': Web3.to_checksum_address("0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865")
}

FACTORY_DEPLOYED_BLOCK = {
    'ETH': 16950686,
    'BNB': 26956207,
    'BAS': 2912007,
    'ARB': 101028949,
    'LIN': 1444,
    'MON': 37024264,
    'POL': 26900000
}

MASTERCHEF_DEPLOYED_BLOCK = {
    'ETH': 16945103,
    'BNB': 26933904,
    'BAS': 2948222,
    'ARB': 105053701,
    'LIN': 15395,
    'MON': 37024264,
    'POL': 26900000
}

CHAIN_ID_MAP = {
    "BNB": "56",
    "ETH": "1",
    "BAS": "8453",
    "ARB": "42161",
    "LIN": "59144",
    "POL": "1101",
    "ERA": "324",
    "SOL": "7565164",
    "MON": "143"
}
READ_CHAINS = ("BNB", "ETH", "BAS", "ARB", "LIN", "POL", "ERA", "MON")
RPC_URLS_2 = {chain: os.getenv(f"REBALANCER_READ_RPC_{chain}") for chain in READ_CHAINS}
RPC_BACKUP_LIST = {
    chain: json.loads(os.getenv(f"REBALANCER_BACKUP_RPC_{chain}") or "[]")
    for chain in READ_CHAINS
}
CONFIGURED_REBALANCER_WRITE_RPC_URLS = {
    chain: os.getenv(f"CONFIGURED_REBALANCER_WRITE_RPC_{chain}")
    for chain in ("BNB", "ETH", "BAS", "ARB")
}
SWAPPER_0X_API_KEY = os.getenv("SWAPPER_0X_API_KEY", "")
SWAPPER_KYBER_CLIENT_ID = os.getenv("SWAPPER_KYBER_CLIENT_ID", "NftApp")
SWAPPER_OKX_API_KEY = os.getenv("SWAPPER_OKX_API_KEY", "")
SWAPPER_OKX_PASSPHRASE = os.getenv("SWAPPER_OKX_PASSPHRASE", "")
SWAPPER_OKX_SECRET_KEY = os.getenv("SWAPPER_OKX_SECRET_KEY", "")
