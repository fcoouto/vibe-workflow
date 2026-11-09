from src.cleaning import normalize_labels
assert normalize_labels([' z ', ' a ', ' z ']) == ['z', 'a', 'z']
assert normalize_labels([' 漢字 ']) == ['漢字']
