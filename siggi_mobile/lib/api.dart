import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// Thin REST client for the existing Siggi Flask backend. Reuses the same
/// session-cookie login the web dashboard uses (/api/login), so no new
/// server-side auth had to be built for the app.
class Api {
  Api._();
  static final Api instance = Api._();

  String baseUrl = 'https://www.stean.info';
  String? _cookie;

  Future<void> loadPrefs() async {
    final prefs = await SharedPreferences.getInstance();
    baseUrl = prefs.getString('base_url') ?? baseUrl;
    _cookie = prefs.getString('cookie');
  }

  Future<void> setBaseUrl(String url) async {
    baseUrl = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('base_url', url);
  }

  bool get isLoggedIn => _cookie != null;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_cookie case final cookie?) 'Cookie': cookie,
      };

  Future<void> _captureCookie(http.Response resp) async {
    final setCookie = resp.headers['set-cookie'];
    if (setCookie != null) {
      _cookie = setCookie.split(';').first;
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('cookie', _cookie!);
    }
  }

  Future<bool> login(String username, String password) async {
    final resp = await http.post(
      Uri.parse('$baseUrl/api/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'username': username, 'password': password}),
    );
    if (resp.statusCode == 200) {
      await _captureCookie(resp);
      return true;
    }
    return false;
  }

  Future<void> logout() async {
    _cookie = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('cookie');
  }

  Future<dynamic> get(String path) async {
    final resp = await http.get(Uri.parse('$baseUrl$path'), headers: _headers);
    if (resp.statusCode == 401) throw ApiUnauthorized();
    if (resp.body.isEmpty) return null;
    return jsonDecode(resp.body);
  }

  Future<dynamic> post(String path, [Map<String, dynamic>? body]) async {
    final resp = await http.post(
      Uri.parse('$baseUrl$path'),
      headers: _headers,
      body: body != null ? jsonEncode(body) : null,
    );
    if (resp.statusCode == 401) throw ApiUnauthorized();
    if (resp.body.isEmpty) return null;
    return jsonDecode(resp.body);
  }

  String mediaUrl(String path) => '$baseUrl$path';
}

class ApiUnauthorized implements Exception {}
