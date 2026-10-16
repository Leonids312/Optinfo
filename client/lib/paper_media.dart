import 'dart:typed_data';

import 'package:http/http.dart' as http;

const paperImageLimit = 12 * 1024 * 1024;

/// The authenticated media endpoint is fixed to the configured server. Never
/// follow redirects: a publisher/source URL must not receive the device token.
Future<Uint8List> fetchPaperImage({
  required String base,
  required String token,
  required String path,
  http.Client? client,
}) async {
  if (!RegExp(r'^/api/media/[a-f0-9]{64}\.png$').hasMatch(path) ||
      token.isEmpty) {
    throw const FormatException('Invalid paper image path');
  }
  final origin = Uri.parse(base);
  final uri = origin.resolve(path);
  if (uri.origin != origin.origin ||
      origin.userInfo.isNotEmpty ||
      (origin.scheme != 'https' &&
          !(origin.scheme == 'http' &&
              (origin.host == 'localhost' || origin.host == '127.0.0.1')))) {
    throw const FormatException('Invalid paper image origin');
  }
  final transport = client ?? http.Client();
  try {
    return await (() async {
      final request = http.Request('GET', uri)
        ..followRedirects = false
        ..headers['Authorization'] = 'Bearer $token'
        ..headers['Accept'] = 'image/png';
      final response = await transport.send(request);
      if (response.statusCode != 200 ||
          response.headers['content-type']?.split(';').first.trim() !=
              'image/png' ||
          (response.contentLength ?? 0) > paperImageLimit) {
        throw const FormatException('Paper image unavailable');
      }
      final bytes = BytesBuilder(copy: false);
      await for (final chunk in response.stream) {
        if (bytes.length + chunk.length > paperImageLimit) {
          throw const FormatException('Paper image is too large');
        }
        bytes.add(chunk);
      }
      final result = bytes.takeBytes();
      const signature = [137, 80, 78, 71, 13, 10, 26, 10];
      if (result.length < 24 ||
          List.generate(8, (i) => result[i] == signature[i]).contains(false)) {
        throw const FormatException('Invalid PNG image');
      }
      return result;
    })()
        .timeout(const Duration(seconds: 25));
  } finally {
    if (client == null) transport.close();
  }
}
