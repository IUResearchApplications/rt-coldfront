def get_user_account_statuses(usernames, resource, accounts=None):
    print("here")
    return dict.fromkeys(usernames, {"exists": True, "reason": "not_enabled"})
