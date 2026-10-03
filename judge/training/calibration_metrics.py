"""Pure empirical action-gate selection; only calibration rows may select gates."""
def decision(row,threshold):
    p=row['probabilities'];best=max(p,key=p.get)
    if best=='match' and p[best]>=threshold['block']:return 'block'
    if best=='no_match' and p[best]>=threshold['allow']:return 'allow'
    return 'review'


def actions(rows,threshold):
    decisions=[(r,decision(r,threshold)) for r in rows]
    return {'rows':len(rows),'block':sum(d=='block' for r,d in decisions),'allow':sum(d=='allow' for r,d in decisions),'review':sum(d=='review' for r,d in decisions),'sensitive_allowed':sum(r['gold']=='match' and d=='allow' for r,d in decisions),'ambiguous_allowed':sum(r['gold']=='insufficient' and d=='allow' for r,d in decisions),'safe_blocked':sum(r['gold']=='no_match' and d=='block' for r,d in decisions),'ambiguous_blocked':sum(r['gold']=='insufficient' and d=='block' for r,d in decisions),'safe_allowed':sum(r['gold']=='no_match' and d=='allow' for r,d in decisions),'sensitive_blocked':sum(r['gold']=='match' and d=='block' for r,d in decisions)}


def calibrate(rows):
    # Each gate accepts only when there are no wrong-class accepts on calibration.
    # >1 means disable that automatic action. This is empirical, not a guarantee.
    candidates=[0.5,0.6,0.7,0.8,0.85,0.9,0.95,0.97,0.99,0.995,0.999,1.000001]
    result={}
    for action,label in [('allow','no_match'),('block','match')]:
        for threshold in candidates:
            accepted=[r for r in rows if r['prediction']==label and r['probabilities'][label]>=threshold]
            if all(r['gold']==label for r in accepted):result[action]=threshold;break
    return result
