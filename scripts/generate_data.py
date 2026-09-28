import argparse, csv, random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260925
SCENARIOS = [
 ("transaction_recognition_confusion","receipt_failed"),("delivery_failure","delivery_failed"),
 ("refund_delay","refund_requested"),("duplicate_looking_payment","close_similar_transaction"),
 ("subscription_billing_confusion","subscription_changed"),("fraud_like","unusual_device"),
 ("merchant_wide_receipt_failure","receipt_failed"),("normal_unusual_transaction","large_amount"),
 ("conflicting_evidence","complaint"),("insufficient_customer_history","first_transaction"),
 ("customer_already_resolved","issue_resolved"),("already_disputed","dispute_filed"),
 ("intervention_failure","intervention_failed"),("intervention_increased_complaints","complaint_after_action"),
 ("high_value_requires_approval","high_value"),("communication_opt_out","opt_out")]

def main():
 p=argparse.ArgumentParser(description="Generate deterministic synthetic DisputeShield CSV data")
 p.add_argument("--seed",type=int,default=SEED); p.add_argument("--customers",type=int,default=5000); p.add_argument("--transactions",type=int,default=50000); p.add_argument("--events",type=int,default=100000); p.add_argument("--output",type=Path,default=Path("data/generated")); a=p.parse_args()
 if a.customers < 1: p.error("customers must be > 0")
 if a.transactions < max(a.customers, 17): p.error("transactions must be >= customers and at least 17")
 rng=random.Random(a.seed); out=a.output; out.mkdir(parents=True,exist_ok=True); now=datetime(2026,9,25,tzinfo=timezone.utc)
 merchants=[f"m{i+1:02d}" for i in range(10)]
 with (out/"merchants.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","name","status"]);w.writerows((m,f"Synthetic Merchant {i+1}","active") for i,m in enumerate(merchants))
 with (out/"customers.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","merchant_id","synthetic_external_id","average_transaction_amount","transaction_count","previous_disputes","previous_refunds","support_contact_count","communication_opt_out"])
  opted_out_customer=15 % a.customers
  for i in range(a.customers): w.writerow([f"c{i+1:06d}",merchants[i%10],f"syn-{i+1:06d}",rng.randint(1800,18000),rng.randint(1,60),rng.choices([0,1,2],[.93,.06,.01])[0],rng.randint(0,3),rng.randint(0,5),int(i==opted_out_customer or rng.random()<.035)])
 customer_ids=[f"c{i+1:06d}" for i in range(a.customers)]; ground={}
 for i,(name,_) in enumerate(SCENARIOS): ground[f"tx-scenario-{i+1:02d}"]=name
 tx_path=out/"transactions.csv"
 with tx_path.open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","merchant_id","customer_id","order_id","amount_minor","currency","payment_method","merchant_descriptor","device_id_hash","country","status","created_at","ground_truth_label"])
  for i in range(a.transactions):
   is_s=i<len(SCENARIOS); ident=f"tx-scenario-{i+1:02d}" if is_s else f"tx-{i+1:06d}"; c=customer_ids[i%a.customers]; merch=merchants[i%10]; amount=rng.randint(500,65000); label=SCENARIOS[i][0] if is_s else "normal"
   if label=="fraud_like": amount=184000
   if label=="high_value_requires_approval": amount=245000
   w.writerow([ident,merch,c,f"o-{i+1:06d}",amount,"USD",rng.choice(["card","wallet","bank"]),f"MERCHANT{i%10+1:02d}*SHOP",f"sha256:{rng.getrandbits(128):032x}",rng.choice(["US","GB","CA","IN"]),"succeeded",(now-timedelta(minutes=rng.randint(1,120000))).isoformat(),label])
 # One synthetic order per transaction; IDs align with transactions.csv.
 with (out/"orders.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","merchant_id","customer_id","transaction_id","order_amount_minor","order_status","delivery_status","promised_delivery_at","delivered_at"])
  for i in range(a.transactions):
   label=SCENARIOS[i][0] if i<len(SCENARIOS) else "normal"; delivery="failed" if label=="delivery_failure" else rng.choice(["delivered","in_transit","processing"])
   w.writerow([f"o-{i+1:06d}",merchants[i%10],customer_ids[i%a.customers],f"tx-scenario-{i+1:02d}" if i<len(SCENARIOS) else f"tx-{i+1:06d}",rng.randint(500,65000),"paid",delivery,(now+timedelta(days=2)).isoformat(),(now-timedelta(days=1)).isoformat() if delivery=="delivered" else ""])
 # A labeled, reproducible dispute sample (including the explicit already-disputed scenario).
 dispute_rng=random.Random(a.seed+101); dispute_indexes=set(dispute_rng.sample(range(max(16,a.transactions)), min(max(0,int(a.transactions*.012)),max(0,a.transactions-16))))
 with (out/"disputes.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","merchant_id","transaction_id","customer_id","reason","amount_minor","status","created_at"])
  selected=sorted(dispute_indexes | ({11} if a.transactions>11 else set()))
  for n,i in enumerate(selected):
   w.writerow([f"d-{n+1:05d}",merchants[i%10],f"tx-scenario-{i+1:02d}" if i<len(SCENARIOS) else f"tx-{i+1:06d}",customer_ids[i%a.customers],dispute_rng.choice(["unrecognized","not_received","duplicate","refund_not_processed"]),dispute_rng.randint(800,55000),"open",(now-timedelta(days=dispute_rng.randint(0,90))).isoformat()])
 with (out/"post_payment_events.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["id","merchant_id","transaction_id","customer_id","event_type","metadata","created_at"])
  event_types=[e for _,e in SCENARIOS]+["receipt_sent","support_contact","invoice_sent","customer_viewed_invoice","delivery_delayed"]
  for i in range(a.events):
   if i<len(SCENARIOS): tx=f"tx-scenario-{i+1:02d}"; event=SCENARIOS[i][1]
   else: tx=f"tx-{rng.randint(17,a.transactions):06d}"; event=rng.choice(event_types)
   ti=int(tx[-6:]) if tx[-6:].isdigit() else 1; merchant=merchants[(ti-1)%10]; customer=customer_ids[(ti-1)%a.customers]
   # Metadata is deliberately plain synthetic data. Consumers must still treat it as untrusted.
   metadata='{"source":"synthetic","note":""}'
   if i==6: metadata='{"incident":"merchant-wide receipt outage"}'
   w.writerow([f"ev-{i+1:07d}",merchant,tx,customer,event,metadata,(now-timedelta(minutes=rng.randint(0,120000))).isoformat()])
 with (out/"scenario_labels.csv").open("w",newline="",encoding="utf-8") as f:
  w=csv.writer(f);w.writerow(["transaction_id","scenario","expected_safety"])
  for i,(name,_) in enumerate(SCENARIOS):w.writerow([f"tx-scenario-{i+1:02d}",name,"human_review_only" if name=="fraud_like" else "policy_gated"])
 print(f"Generated {len(merchants)} merchants, {a.customers} customers, {a.transactions} transactions, {a.events} events at {out.resolve()} (seed={a.seed})")
if __name__=="__main__": main()
