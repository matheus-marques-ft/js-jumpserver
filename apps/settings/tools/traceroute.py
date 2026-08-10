# -*- coding: utf-8 -*-
#
import asyncio
import select
import socket
import struct
import time

from common.utils import get_logger
from settings.utils import generate_ips

logger = get_logger(__name__)
ICMP_PROTO = getattr(socket, 'IPPROTO_ICMP', 1)


def calculate_checksum(data):
    # Compute the ICMP checksum
    checksum = 0
    if len(data) % 2 == 1:
        data += b'\x00'
    for i in range(0, len(data), 2):
        w = (data[i] << 8) + data[i+1]
        checksum += w
    checksum = (checksum >> 16) + (checksum & 0xffff)
    checksum = ~checksum & 0xffff
    return checksum


def recv_with_timeout(sock_fd, timeout):
    ready, _, _ = select.select([sock_fd], [], [], timeout)
    if not ready:
        raise TimeoutError(f'recv timeout after {timeout}s')
    return sock_fd.recvfrom(1024)


async def once_traceroute(target, display, max_hops=30, timeout=3):
    sock_fd = socket.socket(socket.AF_INET, socket.SOCK_RAW, ICMP_PROTO)
    try:
        for ttl in range(1, max_hops+1):
            # Set the TTL
            sock_fd.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            # Send the ICMP Echo request packet
            packet = struct.pack('!BBHHH', 8, 0, 0, 12345, ttl)
            checksum = calculate_checksum(packet)
            packet = struct.pack('!BBHHH', 8, 0, checksum, 12345, ttl)
            start_time = time.monotonic()
            sock_fd.sendto(packet, (target, 0))
            await asyncio.sleep(0.01)
            try:
                # Use select to wait for the raw socket to be readable, avoiding mixing with loop.sock_recvfrom
                recv_packet, addr = await asyncio.to_thread(recv_with_timeout, sock_fd, timeout)
                end_time = time.monotonic()
                # Parse the destination address
                dest_ip = addr[0]
                # Get the hop info
                hop_info = f'{ttl} {dest_ip} ({dest_ip})'
                # Get the delay time info
                delay_info = f'{(end_time - start_time) * 1000:.3f} ms'
                # Print the hop info and delay time
                await display(f'{hop_info:<4} {delay_info}')
                if dest_ip == target:
                    return
            except (TimeoutError, socket.timeout):
                # A timeout occurred, break out of the loop
                await display(f'{ttl} *')
    finally:
        sock_fd.close()


async def verbose_traceroute(dest_ips, timeout=10, display=None):
    if not display:
        return

    ips = generate_ips(dest_ips)
    await display(f'Total valid address: {len(ips)}\r\n')
    for dest_ip in ips:
        await display(f'traceroute to {dest_ip}, 30 hops max, 60 byte packets')
        msg = ''
        try:
            await once_traceroute(dest_ip, display)
        except Exception as e:
            logger.exception('Traceroute failed for %s', dest_ip)
            msg = f'Error: {e}'
        await display(msg)
