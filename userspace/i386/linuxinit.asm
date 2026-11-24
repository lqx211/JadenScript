; linuxinit -- the `/init` a Linux kernel runs out of an initramfs.
;
; The point of it is not what it says. It is that a kernel three layers of
; firmware down from a Scratch project is running a program *this* machine
; assembled, in ring 3, using the kernel's own syscall entry: the message
; appears on COM1 only if the boot protocol, the GDT the kernel rebuilt, the
; paging it turned on, the initramfs it unpacked and the ELF it loaded all
; worked.
;
; `tools/mklinuxiso.py` wraps this flat binary in an ELF32 header (there is no
; ELF linker on this host) and puts it in a newc cpio archive beside a
; `/dev/console`, which is what the kernel opens as this program's stdout --
; `CONFIG_DEVTMPFS_MOUNT` is not set, so the console node has to be in the
; image.

        org     0x00000000
        bits    32

start:
        mov     eax, 4                  ; sys_write
        mov     ebx, 1                  ; stdout, which is /dev/console
        mov     ecx, message
        mov     edx, message_len
        int     0x80

        mov     eax, 4                  ; and again, because a serial line that
        mov     ebx, 1                  ; is read half-way wants a second line
        mov     ecx, message2
        mov     edx, message2_len
        int     0x80

.hang:
        hlt
        jmp     .hang

message:        db "jsw: /init is running in ring 3", 10
message_len     equ $ - message

message2:       db "jsw: the kernel, the pager and the initramfs all worked", 10
message2_len    equ $ - message2
