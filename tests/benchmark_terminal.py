import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

try:
    import ssh_term.rs_term as rs_term
except ImportError:
    pass

def generate_random_styled_text() -> str:
    """Generate a heavy block of dense color and text to break parsing parsers."""
    # This represents pure hell for a terminal sequence parser
    # Constantly shifts background and foreground while rapidly writing
    colors = [31, 32, 33, 34, 35, 36, 91, 94]
    bgs = [40, 41, 44, 45, 100, 104]
    
    body = ""
    for i in range(500):
        # random color shift purely simulated by math offsets
        c = colors[i % len(colors)]
        bg = bgs[(i + 3) % len(bgs)]
        body += f"\x1b[{c};{bg}m{'Term' * 4}\x1b[0m"
    return body + "\r\n" * 2

def run_vte_benchmark():
    print("--- Benchmark: Rust rs_term Core Throughput ---")
    screen = rs_term.Screen(80, 24)
    stream = rs_term.Stream()
    
    chunk_size = 65536
    chunks = 160  # Roughly 10 MB total
    
    payload = generate_random_styled_text()
    # scale up string block to ~chunk_size map
    payload = payload * (chunk_size // len(payload) + 1)
    
    start_time = time.perf_counter()
    for _ in range(chunks):
        stream.feed(screen, payload)
        
    end_time = time.perf_counter()
    duration = end_time - start_time
    total_mb = (len(payload) * chunks) / (1024 * 1024)
    
    print(f"[{total_mb:.2f} MB Heavy Payload Extracted]")
    print(f"Time Taken:      {duration:.4f} seconds")
    print(f"Pure Throughput: {(total_mb / duration):.2f} MB/s -> Very high bandwidth")
    print("")

def run_render_benchmark():
    print("--- Benchmark: Rust Line ANSI Formatting ---")
    screen = rs_term.Screen(80, 24)
    stream = rs_term.Stream()
    stream.feed(screen, generate_random_styled_text() * 10)
    
    start_time = time.perf_counter()
    iterations = 20000
    for _ in range(iterations):
        for y in range(24):
            _ansi = screen.get_line_ansi(y, True, 0)
            
    end_time = time.perf_counter()
    duration = end_time - start_time
    total_lines = iterations * 24
    
    print(f"[{total_lines} ANSI formatted line strings generated cross-boundary]")
    print(f"Time Taken:      {duration:.4f} seconds")
    print(f"Rendering Speed: {(total_lines / duration):.2f} lines/sec")
    print("-> For a 60FPS UI doing 24 lines, this requires min 1440 lines/sec.")

if __name__ == "__main__":
    try:
        run_vte_benchmark()
        run_render_benchmark()
    except NameError:
        print("Required C extension 'ssh_term.rs_term' missing for benchmarking. Build Rust first!")
