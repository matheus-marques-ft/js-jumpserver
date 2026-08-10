from private_storage.servers import NginxXAccelRedirectServer, DjangoServer


class StaticFileServer(object):

    @staticmethod
    def serve(private_file):
        full_path = private_file.full_path
        # todo: after nginx handles a gzip'd recording file, the browser can't parse the content properly,
        # causing online playback to fail; for now, only use nginx to handle mp4 recording files
        if full_path.endswith('.mp4'):
            return NginxXAccelRedirectServer.serve(private_file)
        else:
            return DjangoServer.serve(private_file)
