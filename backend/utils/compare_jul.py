with open("frontend/src/App.tsx", "r", encoding="utf-8") as f:
    for i, line in enumerate(f, 1):
        if "setLocalSymbols" in line or "strategyConfig" in line:
            print(f"{i}: {line.strip().encode('ascii', 'ignore').decode('ascii')}")
