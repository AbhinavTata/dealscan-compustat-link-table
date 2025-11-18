import json
import csv

def main():
    with open('borrower_id_dict.json', 'r') as f:
        data = json.load(f)

    with open('borrower_id_dict.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['GVKEY', 'Dealscan ID', 'Dealscan ID 2'])
        for k, v in data.items():
            ids = [v, 0] if isinstance(v, int) else (v + [0] if len(v) == 1 else v[:2])
            writer.writerow([k] + ids)

if __name__ == '__main__':
    main()