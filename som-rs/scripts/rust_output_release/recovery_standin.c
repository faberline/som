/* Synthetic compiler-shaped service for hosted process tests only. */
#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t listener = -1;
static volatile int build_role = RECOVERY_ROLE;
static void drop_listener(int sig) { (void)sig; if (listener >= 0) close(listener); listener = -1; }

int main(int argc, char **argv) {
    if (build_role == 999) return 8;
    if (argc > 1 && strcmp(argv[1], "serve") != 0) {
        fputs("{\"status\":\"ok\"}", stdout);
        return 0;
    }
    if (argc != 6 || strcmp(argv[2], "--assets") || strcmp(argv[4], "--port")) return 2;
    int port = atoi(argv[5]);
    if (port < 18919 || port >= 19000 || port == 18765 || port == 8765) return 3;
    const char *health = getenv(strstr(argv[0], "/candidate/") || strstr(argv[0], "/versions/")
                                ? "STANDIN_HEALTH_CANDIDATE" : "STANDIN_HEALTH_PREVIOUS");
    if (!health) return 4;
    int fd = socket(AF_INET, SOCK_STREAM, 0), one = 1;
    if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one))) return 5;
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET; addr.sin_port = htons((unsigned short)port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) || listen(fd, 16)) return 6;
    listener = fd;
    struct sigaction action = {0};
    action.sa_handler = drop_listener;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGUSR1, &action, NULL)) return 7;
    for (;;) {
        int client = accept(fd, NULL, NULL);
        if (client < 0) { if (listener < 0) for (;;) pause(); continue; }
        char data[8192]; ssize_t used = 0, n;
        while (used < (ssize_t)sizeof(data) - 1) {
            n = read(client, data + used, sizeof(data) - 1 - (size_t)used);
            if (n <= 0) break;
            used += n; data[used] = 0;
            char *end = strstr(data, "\r\n\r\n");
            if (!end) continue;
            long length = 0; char *len = strstr(data, "Content-Length:");
            if (len) length = strtol(len + 15, NULL, 10);
            if (used >= (end + 4 - data) + length) break;
        }
        data[used] = 0;
        const char *body = !strncmp(data, "GET /healthz ", 13) ? health : "{\"status\":\"ok\"}";
        char header[256];
        int size = snprintf(header, sizeof(header), "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %zu\r\nConnection: close\r\n\r\n", strlen(body));
        if (size > 0) { write(client, header, (size_t)size); write(client, body, strlen(body)); }
        close(client);
    }
}
