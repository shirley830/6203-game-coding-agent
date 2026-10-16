x = 0
print("Position: 0. Type a or d to move, q to quit.")
while True:
    try:
        move = input()
    except EOFError:
        break
    if move == "q":
        print("Bye"); break
    if move in ("a", "left"): x -= 1
    if move in ("d", "right"): x += 1
    print(f"Position: {x}")
