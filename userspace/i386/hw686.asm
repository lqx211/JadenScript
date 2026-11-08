; hw686 -- the instructions a 486 and a 686 added, because a 32-bit Linux will
; not boot on a 386.
;
; The machine said it was a 386DX. A modern kernel refuses: `X86_MINIMUM_CPU_FAMILY`
; is 4, and a distro build asks CPUID about cmov and cx8 before it uses them, so
; the CPU has to answer for a 686 and mean it. This is the blob the two machines
; are compared on -- bswap, cpuid, cmpxchg8b both ways, cmovz/cmovnz, invd and
; wbinvd -- and every one of them is something a kernel executes on the way in.
        org     0x1000
        bits    16
start:
        mov     eax, 0xB1C2D3E4
        bswap   eax                     ; E4 D3 C2 B1
        mov     [0x0500], eax

        mov     eax, 1
        cpuid                           ; family 6, model 8, step 3
        mov     [0x0510], eax
        mov     [0x0520], edx           ; tsc, msr, cx8, cmov

        ; cmpxchg8b that matches: memory becomes the new value
        mov     dword [0x0530], 0x11223344
        mov     dword [0x0534], 0x55667788
        mov     eax, 0x11223344
        mov     edx, 0x55667788
        mov     ebx, 0xDEADBEEF
        mov     ecx, 0xCAFEBABE
        cmpxchg8b [0x0530]              ; equal, so it swaps
        mov     [0x0540], ebx

        ; cmpxchg8b that does not: memory is copied into edx:eax instead
        mov     dword [0x0550], 1
        mov     dword [0x0554], 2
        mov     eax, 0xAAAAAAAA
        mov     edx, 0xBBBBBBBB
        cmpxchg8b [0x0550]
        mov     [0x0560], eax
        mov     [0x0564], edx

        ; cmovz: taken only when ZF is set
        mov     eax, 7
        mov     ebx, 9
        cmp     eax, eax                ; ZF = 1
        cmovz   ebx, eax
        mov     [0x0570], ebx
        mov     eax, 7
        mov     ebx, 9
        cmp     eax, 8                  ; ZF = 0
        cmovz   ebx, eax
        mov     [0x0574], ebx

        ; invd and wbinvd are no-ops here, and must not fault
        wbinvd
        invd
        mov     byte [0x578], 1
        jmp     $
