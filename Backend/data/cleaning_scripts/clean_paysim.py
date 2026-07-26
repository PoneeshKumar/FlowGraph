# this script will clean the paysim csv and reutrn as a .pt so that it can easily load into pytorch and be used for training

import pandas as pd

# this will be used to clean the paysim data and return a .pt file that can be used for training
file = "Backend/data/paysim/paysim.csv"

paysim_df = pd.read_csv(file)

#print(paysim_df.columns)
"""
the columns of the csv are: (['step', 'type', 'amount', 'nameOrig', 'oldbalanceOrg', 'newbalanceOrig',
'nameDest', 'oldbalanceDest', 'newbalanceDest', 'isFraud', 'isFlaggedFraud'],
"""

# for this script we will want to get certain metrics and clean the data so that it can be used for training. We will want to get the following metrics:
"""
1. Amount of transactions per type
2. Average amount of transactions per type
3. Average amount of transactions per type per step
4. Average amount of transactions per type per step per originator
5. Average amount of transactions per type per step per destination
6. Average amount of transactions per type per step per originator per destination
7. Average amount of transactions per type per step per originator per destination per fraud flag
8. Average amount of transactions per type per step per originator per destination per fraud flag per flagged fraud
9. Average amount of transactions per type per step per originator per destination per fraud flag per flagged fraud per old balance originator
10. given this information we will want to create a new dataframe that has the following columns:
['step', 'type', 'amount', 'nameOrig', 'oldbalanceOrg', 'newbalanceOrig', 'nameDest', 'oldbalanceDest', 'newbalanceDest', 'is
Fraud', 'isFlaggedFraud', 'amount_per_type', 'avg_amount_per_type', 'avg_amount_per_type_per_step', 'avg_amount_per_type_per_step_per_originator', 'avg_amount_per_type_per_step_per_destination', 'avg_amount_per_type_per_step_per_originator_per_destination', 'avg_amount_per_type_per_step_per_originator_per_destination_per_fraud_flag', 'avg_amount_per_type_per_step_per_originator_per_destination_per_fraud_flag_per_flagged_fraud', 'avg_amount_per_type_per_step_per_originator_per_destination_per_fraud_flag_per_flagged_fraud_per_old_balance_originator']

then we will use this new dataframe to create a .pt file that can be used for training. We will use the following code to create the .pt file:
"""

avg_amount_transaction_per_type = paysim_df.groupby("type").mean().reset_index()
avg_amount_transaction_per_type_per_step = paysim_df.groupby(["type", "step"]).mean().reset_index()
avg_amount_transaction_per_type_per_step_per_originator = paysim_df.groupby(["type", "step", "nameOrig"]).mean().reset_index()
avg_amount_transaction_per_type_per_step_per_destination = paysim_df.groupby(["type", "step", "nameDest"]).mean().reset_index()
avg_amount_transaction_per_type_per_step_per_destination_per_old_balance_originator = paysim_df.groupby(["type", "step", "nameDest", "oldbalanceOrg"]).mean().reset_index()


paysim_df = paysim_df.merge(avg_amount_transaction_per_type, on="type", suffixes=("", "_avg_amount_per_type"))
paysim_df = paysim_df.merge(avg_amount_transaction_per_type_per_step, on=["type", "step"], suffixes=("", "_avg_amount_per_type_per_step"))
paysim_df = paysim_df.merge(avg_amount_transaction_per_type_per_step_per_originator, on=["type", "step", "nameOrig"], suffixes=("", "_avg_amount_per_type_per_step_per_originator"))
paysim_df = paysim_df.merge(avg_amount_transaction_per_type_per_step_per_destination, on=["type", "step", "nameDest"], suffixes=("", "_avg_amount_per_type_per_step_per_destination"))
paysim_df = paysim_df.merge(avg_amount_transaction_per_type_per_step_per_destination_per_old_balance_originator, on=["type", "step", "nameDest", "oldbalanceOrg"], suffixes=("", "_avg_amount_per_type_per_step_per_destination_per_old_balance_originator"))

#. now we want to transform the dataframe into a .pt file to be used for training

paysim_df.to_pickle("Backend/data/paysim/paysim_cleaned.pt")