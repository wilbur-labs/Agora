#!/bin/bash
# Agora Demo Script — run this to showcase the core workflow
# Usage: bash demo.sh

set -e
cd "$(dirname "$0")/backend"

B="\033[1m"
D="\033[2m"
R="\033[0m"
G="\033[32m"
C="\033[36m"

echo ""
echo -e "${B}🏛  Agora Demo — Multi-Perspective AI Council${R}"
echo -e "${D}Showing: discuss → execute → learn${R}"
echo ""

# Demo 1: Quick answer
echo -e "${B}━━━ Demo 1: Quick Answer (/ask) ━━━${R}"
echo -e "${D}Skips discussion, single agent answers directly${R}"
echo ""
python3 -c "
import asyncio
from agora.api._state import get_council, reset_council
from agora.config.settings import reset_config
from agora.models.registry import reset_registry

async def demo():
    reset_config(); reset_registry(); reset_council()
    c = get_council()
    c.context.add_user('What is Python GIL in one sentence?')
    print('\033[1mYou:\033[0m What is Python GIL in one sentence?')
    print()
    print('\033[1m\033[36m◆ scout\033[0m \033[2m(Researcher)\033[0m')
    async for name, role, chunk in c.stream_quick():
        if chunk: print(chunk, end='', flush=True)
    print('\n')

asyncio.run(demo())
"

# Demo 2: Direct execution
echo -e "${B}━━━ Demo 2: Direct Execution (/exec) ━━━${R}"
echo -e "${D}Skips discussion, executor uses tools directly${R}"
echo ""
python3 -c "
import asyncio, os
from agora.api._state import get_council, reset_council
from agora.config.settings import reset_config
from agora.models.registry import reset_registry

async def demo():
    reset_config(); reset_registry(); reset_council()
    c = get_council()
    c.context.add_user('Create /tmp/agora_demo/hello.txt with content: Hello from Agora!')
    print('\033[1mYou:\033[0m /exec Create hello.txt with content Hello from Agora!')
    print()
    print('\033[1m\033[95m◆ executor\033[0m \033[2m(Task Executor)\033[0m')
    async for name, role, chunk in c.stream_execute():
        if not chunk: continue
        if '[tool_call]' in chunk:
            print(f'  \033[2m🔧 {chunk[12:]}\033[0m')
        elif '[tool_result]' in chunk:
            r = chunk[14:][:150]
            print(f'  \033[2m   → {r}\033[0m')
        elif not chunk.startswith('['):
            print(chunk, end='', flush=True)
    print('\n')
    if os.path.exists('/tmp/agora_demo/hello.txt'):
        print(f'\033[32m✓ File created: {open(\"/tmp/agora_demo/hello.txt\").read()}\033[0m')
        os.unlink('/tmp/agora_demo/hello.txt')
        os.rmdir('/tmp/agora_demo/')
    print()

asyncio.run(demo())
"

echo -e "${B}━━━ Demo Complete ━━━${R}"
echo -e "${D}Try it yourself: cd backend && python3 -m agora${R}"
