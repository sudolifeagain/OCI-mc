package corgitaco.corgilib.client.oci;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.apache.logging.log4j.LogManager;

/** Fetch optional announcements from the author's HTTPS site; 404 means none. */
public final class AnnouncementTransport {
    public static JsonObject fetch(String originalUrl) {
        String url = originalUrl.equals("https://corgitaco.github.io/announcement.json")
                ? "https://corgitaco.dev/announcement.json" : originalUrl;
        try {
            HttpClient client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NORMAL)
                    .connectTimeout(Duration.ofSeconds(10)).build();
            HttpResponse<String> response = client.send(HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofSeconds(15)).GET().build(), HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 404 || response.statusCode() == 410) return null;
            if (response.statusCode() != 200) {
                LogManager.getLogger("CorgiLib").error("Announcement request failed: HTTP {}", response.statusCode());
                return null;
            }
            return JsonParser.parseString(response.body()).getAsJsonObject();
        } catch (Exception error) {
            LogManager.getLogger("CorgiLib").error("Announcement request failed", error);
            return null;
        }
    }
}
